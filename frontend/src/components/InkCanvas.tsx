import { useEffect, useRef } from 'react'

// Ink marbling on paper: fractal noise warped through itself twice, read as bands
// so it forms the rings and threads of ink dropped in water. WebGL2 fragment
// shader; nothing here is needed to use the page, so every failure path simply
// leaves the CSS background showing.

const VERTEX = `#version 300 es
in vec2 a;
void main() { gl_Position = vec4(a, 0.0, 1.0); }`

const FRAGMENT = `#version 300 es
precision highp float;
uniform vec2 u_res;
uniform float u_time;
uniform float u_scroll;
uniform vec2 u_mouse;
uniform vec3 u_paper;
uniform vec3 u_ink;
uniform vec3 u_accent;
uniform float u_portrait;
out vec4 o;

float hash(vec2 p) {
  p = fract(p * vec2(123.34, 456.21));
  p += dot(p, p + 45.32);
  return fract(p.x * p.y);
}

float noise(vec2 p) {
  vec2 i = floor(p), f = fract(p);
  f = f * f * (3.0 - 2.0 * f);
  return mix(mix(hash(i), hash(i + vec2(1, 0)), f.x),
             mix(hash(i + vec2(0, 1)), hash(i + vec2(1, 1)), f.x), f.y);
}

float fbm(vec2 p) {
  float a = 0.5, v = 0.0;
  mat2 m = mat2(1.6, 1.2, -1.2, 1.6);
  for (int i = 0; i < 5; i++) { v += a * noise(p); p = m * p; a *= 0.5; }
  return v;
}

void main() {
  vec2 uv = gl_FragCoord.xy / u_res;
  vec2 p = (gl_FragCoord.xy - 0.5 * u_res) / u_res.y;
  float t = u_time * 0.03;

  // The pointer stirs the water a little.
  vec2 m = (u_mouse - 0.5) * vec2(u_res.x / u_res.y, 1.0);
  p += 0.07 * (p - m) * exp(-4.0 * dot(p - m, p - m));
  p.y += u_scroll * 0.45;

  vec2 q = vec2(fbm(p * 1.25 + vec2(0.0, t)), fbm(p * 1.25 + vec2(5.2, 1.3 - t)));
  vec2 r = vec2(fbm(p * 1.15 + 3.4 * q + vec2(1.7, 9.2) + t * 0.6),
                fbm(p * 1.15 + 3.4 * q + vec2(8.3, 2.8) - t * 0.5));
  float f = fbm(p * 1.05 + 3.1 * r);

  // Rings where the warped field crosses evenly spaced levels.
  float band = abs(fract(f * 6.5 + length(q) * 1.4) - 0.5) * 2.0;
  float line = 1.0 - smoothstep(0.0, 0.1, band);
  float wash = smoothstep(0.42, 0.86, f + 0.28 * r.x);

  // Keep the side the words sit on quiet: right on wide screens, below on tall ones.
  float wide = smoothstep(0.2, 0.9, uv.x + 0.14 * (f - 0.5));
  float tall = smoothstep(0.62, 0.05, uv.y + 0.12 * (f - 0.5));
  float where = mix(wide, tall, u_portrait);

  float ink = clamp(wash * 0.5 + line * 0.6, 0.0, 1.0) * where;
  float vein = smoothstep(0.6, 0.72, r.y) * line * where;

  vec3 col = mix(u_paper, u_ink, ink * 0.84);
  col = mix(col, u_accent, vein * 0.92);
  col += (hash(gl_FragCoord.xy + fract(u_time)) - 0.5) * 0.022;
  o = vec4(col, 1.0);
}`

function cssColor(name: string, fallback: [number, number, number]): [number, number, number] {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  const match = /^#([0-9a-f]{6})$/i.exec(value)
  if (!match) return fallback
  const n = parseInt(match[1], 16)
  return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255]
}

function compile(gl: WebGL2RenderingContext, type: number, source: string): WebGLShader | null {
  const shader = gl.createShader(type)
  if (!shader) return null
  gl.shaderSource(shader, source)
  gl.compileShader(shader)
  return gl.getShaderParameter(shader, gl.COMPILE_STATUS) ? shader : null
}

/** A decorative, living ink background. Hidden from assistive technology. */
export function InkCanvas({ className }: { className?: string }) {
  const ref = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    const canvas = ref.current
    if (!canvas) return
    let gl: WebGL2RenderingContext | null = null
    try {
      gl = canvas.getContext('webgl2', { antialias: false, alpha: false, powerPreference: 'low-power' })
    } catch {
      gl = null
    }
    if (!gl) return

    const vertex = compile(gl, gl.VERTEX_SHADER, VERTEX)
    const fragment = compile(gl, gl.FRAGMENT_SHADER, FRAGMENT)
    const program = gl.createProgram()
    if (!vertex || !fragment || !program) return
    gl.attachShader(program, vertex)
    gl.attachShader(program, fragment)
    gl.linkProgram(program)
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) return
    gl.useProgram(program)

    const buffer = gl.createBuffer()
    gl.bindBuffer(gl.ARRAY_BUFFER, buffer)
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW)
    const position = gl.getAttribLocation(program, 'a')
    gl.enableVertexAttribArray(position)
    gl.vertexAttribPointer(position, 2, gl.FLOAT, false, 0, 0)

    const uniform = (name: string) => gl!.getUniformLocation(program, name)
    const u = {
      res: uniform('u_res'),
      time: uniform('u_time'),
      scroll: uniform('u_scroll'),
      mouse: uniform('u_mouse'),
      paper: uniform('u_paper'),
      ink: uniform('u_ink'),
      accent: uniform('u_accent'),
      portrait: uniform('u_portrait'),
    }

    const still =
      typeof window.matchMedia === 'function' &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches
    const context = gl
    const mouse = { x: 0.7, y: 0.5, tx: 0.7, ty: 0.5 }
    let frame = 0
    let visible = true
    // A fixed, good-looking moment for people who prefer no motion.
    const started = performance.now() - 40_000

    const colours = () => {
      context.uniform3fv(u.paper, cssColor('--bg', [0.95, 0.93, 0.89]))
      context.uniform3fv(u.ink, cssColor('--ink', [0.1, 0.09, 0.07]))
      context.uniform3fv(u.accent, cssColor('--accent', [0.61, 0.14, 0.1]))
    }
    const resize = () => {
      // Capped pixel ratio: the picture is soft by nature and the fill cost is not.
      const ratio = Math.min(window.devicePixelRatio || 1, 1.5)
      const width = Math.max(1, Math.round(canvas.clientWidth * ratio))
      const height = Math.max(1, Math.round(canvas.clientHeight * ratio))
      if (canvas.width !== width || canvas.height !== height) {
        canvas.width = width
        canvas.height = height
      }
      context.viewport(0, 0, width, height)
      context.uniform2f(u.res, width, height)
      context.uniform1f(u.portrait, height > width * 1.05 ? 1 : 0)
    }
    const draw = () => {
      mouse.x += (mouse.tx - mouse.x) * 0.04
      mouse.y += (mouse.ty - mouse.y) * 0.04
      const seconds = still ? 40 : (performance.now() - started) / 1000
      context.uniform1f(u.time, seconds)
      context.uniform1f(u.scroll, Math.min(window.scrollY / Math.max(window.innerHeight, 1), 2))
      context.uniform2f(u.mouse, mouse.x, mouse.y)
      context.drawArrays(context.TRIANGLES, 0, 3)
    }
    const loop = () => {
      draw()
      if (!still && visible && !document.hidden) frame = requestAnimationFrame(loop)
    }
    const restart = () => {
      cancelAnimationFrame(frame)
      resize()
      colours()
      loop()
    }

    const onPointer = (event: PointerEvent) => {
      const box = canvas.getBoundingClientRect()
      mouse.tx = (event.clientX - box.left) / Math.max(box.width, 1)
      mouse.ty = 1 - (event.clientY - box.top) / Math.max(box.height, 1)
    }
    // Only animate while the canvas is on screen and the tab is in front.
    const observer =
      typeof IntersectionObserver === 'function'
        ? new IntersectionObserver(([entry]) => {
            visible = entry.isIntersecting
            if (visible) restart()
          })
        : null
    observer?.observe(canvas)
    const theme = new MutationObserver(restart)
    theme.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })
    window.addEventListener('resize', restart)
    window.addEventListener('pointermove', onPointer, { passive: true })
    document.addEventListener('visibilitychange', restart)
    if (still) window.addEventListener('scroll', draw, { passive: true })
    canvas.dataset.live = 'true'
    restart()

    return () => {
      cancelAnimationFrame(frame)
      observer?.disconnect()
      theme.disconnect()
      window.removeEventListener('resize', restart)
      window.removeEventListener('pointermove', onPointer)
      document.removeEventListener('visibilitychange', restart)
      window.removeEventListener('scroll', draw)
    }
  }, [])

  return <canvas ref={ref} className={className} aria-hidden="true" />
}
