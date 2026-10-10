import { useTheme } from '../lib/theme'
import { Icon } from './icons'

export function ThemeToggle() {
  const { theme, toggle } = useTheme()
  const next = theme === 'dark' ? 'light' : 'dark'
  return (
    <button type="button" className="btn ghost icon" aria-label={`Switch to ${next} theme`} onClick={toggle}>
      <Icon name={theme === 'dark' ? 'sun' : 'moon'} />
    </button>
  )
}
