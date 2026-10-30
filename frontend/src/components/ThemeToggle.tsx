import { useTheme } from '../lib/theme'
import { Icon } from './icons'

export function ThemeToggle() {
  const { theme, toggle } = useTheme()
  const next = theme === 'dark' ? 'light' : 'dark'
  return (
    <button type="button" className="btn ghost sm" aria-label={`Switch to ${next} theme`} onClick={toggle}>
      <Icon name={theme === 'dark' ? 'sun' : 'moon'} size={15} />
      {next === 'dark' ? 'Switch to dark' : 'Switch to light'}
    </button>
  )
}
