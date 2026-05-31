// App header — wordmark, tab nav, version, last-refreshed, theme cycle.
function Header({ tabs, active, onTab, version, lastRefresh, themeMode, onTheme }) {
  const glyph = themeMode === 'dark' ? '☾' : themeMode === 'light' ? '☀' : '◐';
  return (
    <header className="header">
      <div className="header-left">
        <span className="wordmark"><img className="wordmark-mark" src="../../assets/logo.svg" alt="" />HA Ops</span>
        <nav className="tabnav">
          {tabs.map(t => (
            <button key={t.id}
              className={'tab' + (active === t.id ? ' active' : '')}
              onClick={() => onTab(t.id)}>{t.label}</button>
          ))}
        </nav>
      </div>
      <div className="header-right">
        <span className="ver">v{version}</span>
        <span className="refreshed">Last refreshed: {lastRefresh}</span>
        <button className="theme-btn" onClick={onTheme}
          title={'Theme: ' + themeMode + ' (click to cycle)'}>{glyph}</button>
      </div>
    </header>
  );
}
window.Header = Header;
