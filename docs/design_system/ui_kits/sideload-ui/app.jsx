// Root app — tab + theme state, renders Header + active tab.
const TABS = [
  { id: 'timeline', label: 'Timeline' },
  { id: 'backups', label: 'Backups' },
  { id: 'health', label: 'Health' }
];

function App() {
  const [active, setActive] = useState(localStorage.getItem('haops-kit-tab') || 'timeline');
  const [themeMode, setThemeMode] = useState(localStorage.getItem('haops-theme') || 'auto');

  function applyTheme(mode) {
    const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
    const dark = mode === 'dark' || (mode === 'auto' && prefersDark);
    document.documentElement.classList.toggle('dark', dark);
  }
  React.useEffect(() => applyTheme(themeMode), [themeMode]);

  function cycleTheme() {
    const next = themeMode === 'auto' ? 'light' : themeMode === 'light' ? 'dark' : 'auto';
    setThemeMode(next);
    if (next === 'auto') localStorage.removeItem('haops-theme');
    else localStorage.setItem('haops-theme', next);
  }
  function tab(id) { setActive(id); localStorage.setItem('haops-kit-tab', id); }

  return (
    <div className="app">
      <Header tabs={TABS} active={active} onTab={tab}
        version={window.HAOPS_DATA.version}
        lastRefresh={new Date().toLocaleTimeString()}
        themeMode={themeMode} onTheme={cycleTheme} />
      <main className="main">
        {active === 'timeline' && <TimelineTab />}
        {active === 'backups' && <BackupsTab />}
        {active === 'health' && <HealthTab />}
      </main>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(<App />);
