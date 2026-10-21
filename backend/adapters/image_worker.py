"""The picture decoder, run as its own short-lived process (AST-006).

This file is executed as a script by ``image_processor.py`` with ``python -I`` (no
environment variables, no user site, the current directory not on the import path) and
an empty environment, so it can import nothing but the standard library and Pillow, and
it sees no secret. Before Pillow is even imported it lowers its own limits: address
space, CPU seconds, no files may be written, no core dumps. The parent also stops it
after a wall-clock timeout.

It reads the picture from standard input and writes one JSON object to standard output:
either the smaller copies (WebP, base64) with the picture's size and format, or a short
failure code. It never writes the picture's content, or an error's message, anywhere
else: an error message from a decoder can quote the file.

Decoding follows Pillow's security handbook
(https://pillow.readthedocs.io/en/latest/handbook/security.html): the byte count is
capped before decoding; only PNG, JPEG and WebP are tried (``Image.open(formats=...)``);
``MAX_IMAGE_PIXELS`` is set and a ``DecompressionBombWarning`` is an error; each side
and the pixel count are checked from the header before the pixels are read; a JPEG is
read at a reduced scale when only small copies are wanted. The orientation a photo
carries is applied, and every copy is written from bare pixels, so no EXIF, XMP, text
or colour profile reaches it. A 16-bit greyscale picture is scaled to 8 bits, and a
transparent colour becomes an alpha channel. To stay inside the memory limit the
full-size pixels are held once: nothing is copied that does not need to be, and each
copy is made from the larger one before it.

The sandbox (AST-006, ADR-010 amendment 2026-10-21). After the limits, and after Pillow and
everything decoding will need have been loaded (a few tiny pictures made in memory are
decoded first, so no import happens later), the worker shuts itself in, each layer only
where this machine has it, and none of them able to stop the worker working elsewhere:

* ``no_new_privs`` (``prctl``), which the two layers below need;
* a network namespace of its own (``unshare``), so no network exists; usually refused
  inside a container, where the seccomp layer covers it;
* Landlock (Linux 5.13 or later) with no path allowed: no file or directory can be
  opened, made, removed or run, and from its version 4 no TCP bind or connect, from
  version 6 no signals or abstract sockets outside the process;
* a seccomp filter written here as classic BPF (x86-64 and arm64): opening, making or
  removing files, sockets, running programs, starting processes, tracing, mounting and
  the like fail with "operation not permitted".

Standard input, output and error were opened before and keep working. The answer says
which layers took hold; ``sandbox: "require"`` refuses to decode without Landlock and
seccomp, ``"off"`` applies none. Kernel documentation:
https://docs.kernel.org/userspace-api/landlock.html and
https://docs.kernel.org/userspace-api/seccomp_filter.html.
"""

from __future__ import annotations

import base64
import ctypes
import io
import json
import os
import platform
import socket
import struct
import sys
import warnings
from typing import Any

ALLOWED_FORMATS = ("PNG", "JPEG", "WEBP")


class Rejected(Exception):
    """The picture cannot be used; ``code`` says why in a fixed vocabulary."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _looks_allowed(data: bytes) -> bool:
    """Whether the leading bytes are those of a PNG, JPEG or WebP file."""
    return (
        data.startswith(b"\x89PNG\r\n\x1a\n")
        or data.startswith(b"\xff\xd8\xff")
        or (len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP")
    )


# EXIF orientation -> Pillow's ``Image.Transpose`` value (5 to 8 swap the sides).
_TRANSPOSE: dict[int, int] = {2: 0, 3: 3, 4: 1, 5: 5, 6: 4, 7: 6, 8: 2}
_QUARTER_TURNS = frozenset({2, 4, 5, 6})  # ROTATE_90, ROTATE_270, TRANSPOSE, TRANSVERSE


def bare_pixels(image: Any) -> Any:
    """The picture as 8-bit RGB, or RGBA when any of it is see-through.

    A 16-bit greyscale PNG is scaled down to 8 bits first (a plain conversion clips
    everything above 255 to white), and a single transparent colour (a PNG ``tRNS``
    chunk, in any mode) becomes an alpha channel rather than being dropped. An image
    already in the wanted mode is used as it is, not copied."""
    from PIL import Image, ImageMath

    mode = str(image.mode)
    transparency = image.info.get("transparency")
    if mode == "I" or mode.startswith("I;16"):
        wide = image if mode == "I" else image.convert("I")
        grey = wide.point(lambda value: value * (1 / 256)).convert("L")
        if isinstance(transparency, int):
            alpha = ImageMath.lambda_eval(
                lambda args: (args["wide"] != transparency) * 255, wide=wide
            ).convert("L")
            return Image.merge("RGBA", (grey, grey, grey, alpha))
        return grey.convert("RGB")
    has_alpha = mode in ("RGBA", "RGBa", "LA", "La", "PA") or transparency is not None
    target = "RGBA" if has_alpha else "RGB"
    if mode == target and transparency is None:
        return image
    return image.convert(target)


def limit_resources(limits: dict[str, Any]) -> None:
    """Lower this process's own limits; it cannot raise them again."""
    import resource

    memory = int(limits.get("memory_bytes") or 0)
    if memory > 0:
        resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
    cpu = int(limits.get("cpu_seconds") or 0)
    if cpu > 0:
        # The soft limit sends SIGXCPU; the hard limit a second later kills outright.
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 1))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


# -- The sandbox --------------------------------------------------------------------

_PR_SET_NO_NEW_PRIVS = 38
_PR_SET_SECCOMP = 22
_SECCOMP_MODE_FILTER = 2
_CLONE_NEWNET = 0x40000000
_CLONE_NEWUSER = 0x10000000
_CLONE_THREAD = 0x00010000
_EPERM = 1
_ENOSYS = 38

# Landlock's system calls have the same numbers on every architecture that has them.
_LANDLOCK_CREATE_RULESET = 444
_LANDLOCK_RESTRICT_SELF = 446
_LANDLOCK_CREATE_RULESET_VERSION = 1

# audit architecture -> (denied system calls, ``clone`` number, highest valid number + 1)
_AUDIT_ARCH_X86_64 = 0xC000003E
_AUDIT_ARCH_AARCH64 = 0xC00000B7
# Files and directories by path; the network; programs and processes; tracing or
# signalling another process (every way to send a signal, and to reach a process by a
# pidfd); mounting, namespaces, modules, keys and other kernel interfaces a picture
# decoder never needs. ``stat`` and its relatives stay allowed: Python's own I/O calls
# them on open descriptors (a known limit: a path's existence can still be asked).
_DENY_X86_64 = (
    2,
    85,
    257,
    437,
    303,
    304,  # open creat openat openat2 name_to/open_by_handle_at
    83,
    258,
    133,
    259,
    84,
    87,
    263,
    82,
    264,
    316,  # mkdir* mknod* rmdir unlink* rename*
    86,
    265,
    88,
    266,
    90,
    268,
    452,
    92,
    94,
    260,
    76,  # link* symlink* chmod* chown* truncate
    41,
    53,
    42,
    49,
    50,
    43,
    288,  # socket socketpair connect bind listen accept accept4
    59,
    322,
    57,
    58,  # execve execveat fork vfork
    101,
    310,
    311,
    62,  # ptrace process_vm_readv/writev kill
    200,
    234,
    129,
    297,  # tkill tgkill rt_sigqueueinfo rt_tgsigqueueinfo: signals to other processes
    424,
    434,
    438,  # pidfd_send_signal pidfd_open pidfd_getfd
    165,
    166,
    155,
    161,
    272,
    308,  # mount umount2 pivot_root chroot unshare setns
    428,
    429,
    430,
    431,
    432,
    433,
    442,  # the new mount interface
    321,
    298,
    425,
    426,
    427,
    323,  # bpf perf_event_open io_uring_* userfaultfd
    248,
    249,
    250,  # add_key request_key keyctl
    175,
    313,
    176,
    246,
    320,
    169,
    167,
    168,
    163,
    179,  # modules kexec reboot swap acct quota
)
_DENY_AARCH64 = (
    56,
    437,
    264,
    265,  # openat openat2 name_to/open_by_handle_at
    34,
    33,
    35,
    38,
    276,
    37,
    36,
    53,
    452,
    54,
    45,  # *at forms, truncate
    198,
    199,
    203,
    200,
    201,
    202,
    242,  # sockets
    221,
    281,  # execve execveat
    117,
    270,
    271,
    129,  # ptrace process_vm_readv/writev kill
    130,
    131,
    138,
    240,  # tkill tgkill rt_sigqueueinfo rt_tgsigqueueinfo: signals to other processes
    424,
    434,
    438,  # pidfd_send_signal pidfd_open pidfd_getfd
    40,
    39,
    41,
    51,
    97,
    268,  # mount umount2 pivot_root chroot unshare setns
    428,
    429,
    430,
    431,
    432,
    433,
    442,
    280,
    241,
    425,
    426,
    427,
    282,
    217,
    218,
    219,
    105,
    273,
    106,
    104,
    294,
    142,
    224,
    225,
    89,
    60,
)
_ARCHES: dict[str, tuple[int, tuple[int, ...], int, int | None]] = {
    # machine -> (audit arch, denied, clone, clone3)
    "x86_64": (_AUDIT_ARCH_X86_64, _DENY_X86_64, 56, 435),
    "aarch64": (_AUDIT_ARCH_AARCH64, _DENY_AARCH64, 220, 435),
}


def _libc() -> Any:
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    return libc


def _landlock(libc: Any) -> dict[str, Any]:
    """Allow no path at all; returns the Landlock version used, or why there is none."""
    syscall = libc.syscall
    abi = syscall(
        ctypes.c_long(_LANDLOCK_CREATE_RULESET),
        None,
        ctypes.c_size_t(0),
        ctypes.c_uint32(_LANDLOCK_CREATE_RULESET_VERSION),
    )
    if abi < 1:
        return {"active": False, "reason": "not_supported"}
    fs = (1 << 13) - 1  # execute, write, read, read dir, remove, make (version 1)
    if abi >= 2:
        fs |= 1 << 13  # refer
    if abi >= 3:
        fs |= 1 << 14  # truncate
    if abi >= 5:
        fs |= 1 << 15  # ioctl on devices
    net = 0b11 if abi >= 4 else 0  # bind and connect TCP
    scoped = 0b11 if abi >= 6 else 0  # abstract unix sockets, signals
    fields = [fs] + ([net] if abi >= 4 else []) + ([scoped] if abi >= 6 else [])
    attr = (ctypes.c_uint64 * len(fields))(*fields)
    ruleset = syscall(
        ctypes.c_long(_LANDLOCK_CREATE_RULESET),
        ctypes.byref(attr),
        ctypes.c_size_t(ctypes.sizeof(attr)),
        ctypes.c_uint32(0),
    )
    if ruleset < 0:
        return {"active": False, "reason": "refused"}
    try:
        done = syscall(
            ctypes.c_long(_LANDLOCK_RESTRICT_SELF), ctypes.c_int(ruleset), ctypes.c_uint32(0)
        )
    finally:
        os.close(ruleset)
    if done != 0:
        return {"active": False, "reason": "refused"}
    return {"active": True, "version": int(abi)}


def seccomp_program(machine: str) -> list[tuple[int, int, int, int]] | None:
    """The classic BPF filter for ``machine`` as (code, jt, jf, k), or None if unknown."""
    arch = _ARCHES.get(machine)
    if arch is None:
        return None
    audit_arch, denied, clone, clone3 = arch
    ld_abs = 0x20  # BPF_LD | BPF_W | BPF_ABS
    jeq = 0x15  # BPF_JMP | BPF_JEQ | BPF_K
    jge = 0x35  # BPF_JMP | BPF_JGE | BPF_K
    jset = 0x45  # BPF_JMP | BPF_JSET | BPF_K
    ret = 0x06  # BPF_RET | BPF_K
    allow = 0x7FFF0000
    kill = 0x80000000  # SECCOMP_RET_KILL_PROCESS
    eperm = 0x00050000 | _EPERM  # SECCOMP_RET_ERRNO
    enosys = 0x00050000 | _ENOSYS
    program: list[tuple[int, int, int, int]] = [
        (ld_abs, 0, 0, 4),  # arch
        (jeq, 1, 0, audit_arch),
        (ret, 0, 0, kill),  # another ABI (32-bit calls on a 64-bit kernel): stop outright
        (ld_abs, 0, 0, 0),  # system call number
    ]
    if machine == "x86_64":
        program += [(jge, 0, 1, 0x40000000), (ret, 0, 0, eperm)]  # the x32 ABI
    for number in denied:
        program += [(jeq, 0, 1, number), (ret, 0, 0, eperm)]
    if clone3 is not None:
        # Its flags are behind a pointer the filter cannot read; C libraries fall back
        # to ``clone`` when told it does not exist.
        program += [(jeq, 0, 1, clone3), (ret, 0, 0, enosys)]
    # ``clone`` may start a thread, never a new process.
    program += [
        (jeq, 0, 4, clone),
        (ld_abs, 0, 0, 16),  # low half of the first argument (little-endian)
        (jset, 1, 0, _CLONE_THREAD),
        (ret, 0, 0, eperm),
        (ret, 0, 0, allow),
        (ret, 0, 0, allow),
    ]
    return program


def _seccomp(libc: Any) -> dict[str, Any]:
    program = seccomp_program(platform.machine())
    if program is None:
        return {"active": False, "reason": "unknown_architecture"}

    class SockFilter(ctypes.Structure):
        _fields_ = [
            ("code", ctypes.c_ushort),
            ("jt", ctypes.c_ubyte),
            ("jf", ctypes.c_ubyte),
            ("k", ctypes.c_uint32),
        ]

    class SockFprog(ctypes.Structure):
        _fields_ = [("len", ctypes.c_ushort), ("filter", ctypes.POINTER(SockFilter))]

    filters = (SockFilter * len(program))(*(SockFilter(*item) for item in program))
    fprog = SockFprog(len(program), filters)
    done = libc.prctl(
        ctypes.c_int(_PR_SET_SECCOMP),
        ctypes.c_ulong(_SECCOMP_MODE_FILTER),
        ctypes.byref(fprog),
        ctypes.c_ulong(0),
        ctypes.c_ulong(0),
    )
    if done != 0:
        return {"active": False, "reason": "refused"}
    return {"active": True, "denied": len(_ARCHES[platform.machine()][1])}


def apply_sandbox(mode: str) -> dict[str, Any]:
    """Shut this process in as far as the machine allows; says which layers took hold.

    Never raises: a missing layer is reported, not fatal, unless ``mode`` is
    ``"require"`` (the caller then refuses to decode)."""
    layers: dict[str, Any] = {
        "no_new_privs": False,
        "network_namespace": False,
        "landlock": {"active": False, "reason": "off"},
        "seccomp": {"active": False, "reason": "off"},
    }
    if mode == "off" or not sys.platform.startswith("linux"):
        if mode != "off":
            layers["landlock"]["reason"] = layers["seccomp"]["reason"] = "not_linux"
        return {"mode": mode, "layers": layers, "level": "limits_only"}
    try:
        libc = _libc()
    except Exception:  # noqa: BLE001 - no C library to call: limits only
        layers["landlock"]["reason"] = layers["seccomp"]["reason"] = "no_libc"
        return {"mode": mode, "layers": layers, "level": "limits_only"}
    try:
        layers["no_new_privs"] = (
            libc.prctl(
                ctypes.c_int(_PR_SET_NO_NEW_PRIVS),
                ctypes.c_ulong(1),
                ctypes.c_ulong(0),
                ctypes.c_ulong(0),
                ctypes.c_ulong(0),
            )
            == 0
        )
    except Exception:  # noqa: BLE001
        layers["no_new_privs"] = False
    try:
        layers["network_namespace"] = (
            libc.unshare(ctypes.c_int(_CLONE_NEWUSER | _CLONE_NEWNET)) == 0
        )
    except Exception:  # noqa: BLE001
        layers["network_namespace"] = False
    if layers["no_new_privs"]:
        try:
            layers["landlock"] = _landlock(libc)
        except Exception:  # noqa: BLE001
            layers["landlock"] = {"active": False, "reason": "failed"}
        try:
            layers["seccomp"] = _seccomp(libc)
        except Exception:  # noqa: BLE001
            layers["seccomp"] = {"active": False, "reason": "failed"}
    else:
        layers["landlock"]["reason"] = layers["seccomp"]["reason"] = "no_new_privs_refused"
    landlock = bool(layers["landlock"].get("active"))
    seccomp = bool(layers["seccomp"].get("active"))
    level = (
        "full" if landlock and seccomp else "partial" if landlock or seccomp else ("limits_only")
    )
    return {"mode": mode, "layers": layers, "level": level}


def warm_up() -> None:
    """Load every module decoding can reach, by decoding tiny pictures made here.

    Pillow imports its format plugins and some helpers on first use; once the sandbox
    is on, no file can be opened, so an import then would fail."""
    from PIL import Image, ImageMath  # noqa: F401

    Image.init()
    samples: list[bytes] = []
    small = Image.new("RGB", (4, 2), (10, 20, 30))
    exif = Image.Exif()
    exif[0x0112] = 6
    for fmt, extra in (("PNG", {}), ("JPEG", {"exif": exif.tobytes()}), ("WEBP", {})):
        out = io.BytesIO()
        small.save(out, format=fmt, **extra)
        samples.append(out.getvalue())
    wide = Image.new("I;16", (2, 2), 300)
    wide.info["transparency"] = 300
    out = io.BytesIO()
    wide.save(out, format="PNG", transparency=300)
    samples.append(out.getvalue())
    out = io.BytesIO()
    Image.new("P", (2, 2), 1).save(out, format="PNG", transparency=1)
    samples.append(out.getvalue())
    limits = {
        "max_bytes": 1 << 20,
        "max_pixels": 1 << 20,
        "max_dimension": 64,
        "sizes": {"a": 2, "b": 1},
    }
    for sample in samples:
        render(sample, limits)
    for bad in (b"\x89PNG\r\n\x1a\n" + b"\0" * 16, b"not a picture at all"):
        try:
            render(bad, limits)
        except Rejected:
            pass
    json.dumps({"ok": True})


def probe() -> dict[str, Any]:
    """What a sandboxed worker can still do (for health and tests): each is True when it
    was refused. Opens and connects nothing that matters; never reads a file's content."""
    refused: dict[str, bool] = {}
    try:
        os.close(os.open("/", os.O_RDONLY))
        refused["read_files"] = False
    except OSError:
        refused["read_files"] = True
    path = f"/tmp/.novel-engine-probe-{os.getpid()}-{os.urandom(4).hex()}"  # noqa: S108
    try:
        os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
    except OSError:
        refused["write_files"] = True
    else:
        refused["write_files"] = False
        try:
            os.unlink(path)
        except OSError:
            pass
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("127.0.0.1", 9))
            refused["network"] = False
        finally:
            sock.close()
    except OSError:
        refused["network"] = True
    try:
        pid = os.fork()
    except OSError:
        refused["new_process"] = True
    else:
        if pid == 0:
            os._exit(0)
        os.waitpid(pid, 0)
        refused["new_process"] = False
    return refused


def render(data: bytes, limits: dict[str, Any]) -> dict[str, Any]:
    """Decode one picture within ``limits`` and make its smaller copies."""
    from PIL import Image, UnidentifiedImageError

    max_bytes = int(limits["max_bytes"])
    max_pixels = int(limits["max_pixels"])
    max_dimension = int(limits["max_dimension"])
    sizes: dict[str, int] = {str(k): int(v) for k, v in dict(limits["sizes"]).items()}
    quality = int(limits.get("quality", 80))

    if not data:
        raise Rejected("damaged")
    if len(data) > max_bytes:
        raise Rejected("too_large")

    Image.MAX_IMAGE_PIXELS = max_pixels
    warnings.simplefilter("error", Image.DecompressionBombWarning)
    damaged = (OSError, SyntaxError, ValueError, struct.error, EOFError, IndexError)

    try:
        image: Image.Image = Image.open(io.BytesIO(data), formats=list(ALLOWED_FORMATS))
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise Rejected("too_many_pixels") from error
    except UnidentifiedImageError as error:
        # The file says it is one of ours but cannot be followed: cut short or broken.
        raise Rejected("damaged" if _looks_allowed(data) else "unsupported_type") from error
    except damaged as error:
        raise Rejected("damaged") from error

    detected = str(image.format or "")
    if detected not in ALLOWED_FORMATS:
        raise Rejected("unsupported_type")
    width, height = image.size
    if width <= 0 or height <= 0:
        raise Rejected("damaged")
    if width > max_dimension or height > max_dimension or width * height > max_pixels:
        raise Rejected("too_many_pixels")

    largest = max(sizes.values())
    try:
        if getattr(image, "n_frames", 1) > 1:
            image.seek(0)  # a moving picture: its first frame only
        if detected == "JPEG":
            # Decode at the smallest power-of-two scale still at least this large.
            image.draft(image.mode, (largest, largest))
        image.load()
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise Rejected("too_many_pixels") from error
    except damaged as error:
        raise Rejected("damaged") from error

    # Turned the right way up by hand, not with ``exif_transpose``: that rewrites the
    # metadata after moving the pixels, so a failure there could leave the two disagreeing.
    # The size reported is the size of what was actually produced.
    try:
        orientation = int(image.getexif().get(0x0112, 1) or 1)
    except Exception:  # noqa: BLE001 - an unreadable orientation is ignored, not fatal
        orientation = 1
    method = _TRANSPOSE.get(orientation)
    if method is not None:
        try:
            # Rebinding drops the unturned pixels: only one full-size copy is kept.
            image = image.transpose(Image.Transpose(method))
        except MemoryError:
            raise
        except Exception:  # noqa: BLE001 - shown as stored rather than not at all
            method = None
    if method in _QUARTER_TURNS:
        width, height = height, width

    try:
        bare = bare_pixels(image)
    except damaged as error:
        raise Rejected("damaged") from error
    del image
    # Nothing the file carried travels with the pixels.
    bare.info.clear()

    # The largest copy is made first and each smaller one from the copy before it, in
    # place, so the full-size pixels are never held twice.
    variants: list[dict[str, Any]] = []
    current = bare
    for kind, side in sorted(sizes.items(), key=lambda item: -item[1]):
        current.thumbnail((side, side), Image.Resampling.LANCZOS)
        current.info.clear()
        out = io.BytesIO()
        current.save(out, format="WEBP", quality=quality, method=4)
        variants.append(
            {
                "kind": kind,
                "width": current.width,
                "height": current.height,
                "data": base64.b64encode(out.getvalue()).decode("ascii"),
            }
        )
    variants.reverse()
    return {
        "ok": True,
        "width": width,
        "height": height,
        "format": detected,
        "variants": variants,
    }


def main() -> int:
    limits = json.loads(sys.argv[1])
    limit_resources(limits)
    mode = str(limits.get("sandbox") or "best")
    data = sys.stdin.buffer.read(int(limits["max_bytes"]) + 1)
    try:
        if mode != "off":
            warm_up()
        sandbox = apply_sandbox(mode)
    except Exception:  # noqa: BLE001 - only a fixed code leaves this process
        sys.stdout.buffer.write(b'{"ok": false, "code": "processor_failed"}')
        return 0
    if mode == "require" and sandbox["level"] != "full":
        answer: dict[str, Any] = {"ok": False, "code": "sandbox_unavailable"}
        answer["sandbox"] = sandbox
        sys.stdout.buffer.write(json.dumps(answer).encode("ascii"))
        return 0
    if limits.get("probe"):
        answer = {"ok": True, "sandbox": sandbox, "refused": probe()}
        sys.stdout.buffer.write(json.dumps(answer).encode("ascii"))
        sys.stdout.buffer.flush()
        return 0
    try:
        answer = render(data, limits)
    except Rejected as rejected:
        answer = {"ok": False, "code": rejected.code}
    except MemoryError:
        answer = {"ok": False, "code": "out_of_memory"}
    except Exception:  # noqa: BLE001 - only a fixed code leaves this process
        answer = {"ok": False, "code": "processor_failed"}
    answer["sandbox"] = sandbox["level"]
    sys.stdout.buffer.write(json.dumps(answer).encode("ascii"))
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
