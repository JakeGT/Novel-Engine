/**
 * The Novel Engine seal: a drop of ink that is also a quotation mark, with the
 * rings ink makes when it meets water. Keep in step with `public/favicon.svg`.
 */
export function Logo({ size = 30 }: { size?: number }) {
  return (
    <svg className="logo" width={size} height={size} viewBox="0 0 64 64" aria-hidden="true" focusable="false">
      <rect width="64" height="64" rx="13" fill="#a3271b" />
      <path
        d="M43.5 8.5c-1.2 10.3 7.8 15.7 7.8 27.3a19.3 19.3 0 1 1-38.6 0c0-9.4 7-14.4 15.8-16.9 6.6-1.9 12.4-4.7 15-10.4Z"
        fill="#f9f5ec"
      />
      <g fill="none" stroke="#a3271b" strokeLinecap="round" strokeWidth={2.6}>
        <path d="M22.3 39.2a10.2 10.2 0 0 1 16.8-10.3" />
        <path d="M41.8 33.8a10.2 10.2 0 0 1-15.9 11.8" />
        <circle cx="32" cy="37.3" r="3.6" />
      </g>
    </svg>
  )
}
