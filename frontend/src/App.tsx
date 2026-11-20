import { useEffect, useState } from "react";
import {
  Activity,
  BookOpen,
  ChartNoAxesCombined,
  ClipboardCheck,
  Database,
  FlaskConical,
  History,
  LayoutDashboard,
  ListTree,
  Moon,
  Orbit,
  PanelLeft,
  Search,
  Settings,
  Sun,
  Workflow,
  Wrench,
} from "lucide-react";
import {
  BrowserRouter,
  NavLink,
  Navigate,
  Route,
  Routes,
} from "react-router-dom";
import type { Principal } from "./api/rag";
import { errorText } from "./api/resource";
import { AuthGate } from "./components/auth";
import { Button } from "./components/ui/button";
import Playground from "./Playground";
import { LearningCenter, TeacherCenter } from "./pages/Administration";
import { DocumentInspector, KnowledgeBase } from "./pages/Knowledge";
import {
  Analytics,
  Dashboard,
  MemoryCenter,
  ModelRegistry,
  SettingsPage,
  WorkflowManager,
} from "./pages/Operations";
import { BenchmarkManager, ComparisonLab, Experiments } from "./pages/Research";

const groups = [
  {
    title: "Workspace",
    items: [
      { path: "/", label: "Dashboard", icon: LayoutDashboard },
      { path: "/playground", label: "RAG Playground", icon: Search },
      { path: "/knowledge", label: "Knowledge Base", icon: Database },
      { path: "/inspector", label: "Document Inspector", icon: BookOpen },
    ],
  },
  {
    title: "Research",
    items: [
      { path: "/teacher", label: "Teacher Center", icon: ClipboardCheck },
      { path: "/comparison", label: "RAG Comparison Lab", icon: ListTree },
      { path: "/experiments", label: "Experiments", icon: FlaskConical },
      { path: "/benchmarks", label: "Benchmark Manager", icon: Activity },
      { path: "/analytics", label: "Analytics", icon: ChartNoAxesCombined },
    ],
  },
  {
    title: "Administration",
    items: [
      { path: "/learning", label: "Learning Center", icon: Wrench },
      { path: "/memory", label: "Memory Center", icon: History },
      { path: "/models", label: "Model Registry", icon: Orbit },
      { path: "/workflows", label: "Workflow Manager", icon: Workflow },
      { path: "/settings", label: "Settings", icon: Settings },
    ],
  },
];

type Theme = "dark" | "light";

export default function App() {
  const [theme, setTheme] = useState<Theme>(() =>
    localStorage.getItem("evorag-theme") === "light" ? "light" : "dark",
  );
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("evorag-theme", theme);
  }, [theme]);
  return (
    <BrowserRouter>
      <AuthGate>
        {(user, signOut) => (
          <Shell
            user={user}
            theme={theme}
            setTheme={setTheme}
            signOut={signOut}
          />
        )}
      </AuthGate>
    </BrowserRouter>
  );
}

function Shell({
  user,
  theme,
  setTheme,
  signOut,
}: {
  user: Principal;
  theme: Theme;
  setTheme: (theme: Theme) => void;
  signOut: () => Promise<void>;
}) {
  const [collapsed, setCollapsed] = useState(
    () => matchMedia("(max-width: 760px)").matches,
  );
  const [error, setError] = useState("");
  const [signingOut, setSigningOut] = useState(false);
  const admin = user.roles.includes("admin");

  async function leave() {
    setSigningOut(true);
    setError("");
    try {
      await signOut();
    } catch (failure) {
      setError(errorText(failure));
    } finally {
      setSigningOut(false);
    }
  }

  return (
    <div className="dashboard-shell" data-collapsed={collapsed}>
      <aside className="sidebar">
        <div className="brand-row">
          <NavLink
            to="/"
            className="sidebar-brand"
            aria-label="EvoRAG dashboard"
          >
            <Orbit size={25} aria-hidden />
            <span>EvoRAG</span>
          </NavLink>
          <Button
            variant="ghost"
            aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            aria-expanded={!collapsed}
            onClick={() => setCollapsed(!collapsed)}
          >
            <PanelLeft size={18} />
          </Button>
        </div>
        <nav aria-label="Main navigation" className="sidebar-nav">
          {groups.map((group) => (
            <div className="nav-group" key={group.title}>
              <h2>{group.title}</h2>
              {group.items.map(({ path, label, icon: Icon }) => (
                <NavLink
                  to={path}
                  end={path === "/"}
                  key={path}
                  title={label}
                  aria-label={label}
                  className={({ isActive }) =>
                    isActive ? "nav-item active" : "nav-item"
                  }
                  onClick={() => {
                    if (matchMedia("(max-width: 760px)").matches)
                      setCollapsed(true);
                  }}
                >
                  <Icon size={19} aria-hidden />
                  <span>{label}</span>
                </NavLink>
              ))}
            </div>
          ))}
        </nav>
      </aside>
      <div className="shell-content">
        <header className="topbar">
          <span className="topbar-account">{user.display_name}</span>
          <Button
            variant="ghost"
            aria-label={theme === "dark" ? "Use light theme" : "Use dark theme"}
            onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
          >
            {theme === "dark" ? <Sun size={18} /> : <Moon size={18} />}
          </Button>
          <Button
            variant="outline"
            disabled={signingOut}
            onClick={() => void leave()}
          >
            Sign out
          </Button>
        </header>
        <main className="page-content">
          {error && (
            <p role="alert" className="form-error">
              {error}
            </p>
          )}
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/playground" element={<Playground />} />
            <Route path="/knowledge" element={<KnowledgeBase />} />
            <Route path="/inspector" element={<DocumentInspector />} />
            <Route path="/teacher" element={<TeacherCenter />} />
            <Route path="/learning" element={<LearningCenter user={user} />} />
            <Route path="/comparison" element={<ComparisonLab />} />
            <Route path="/experiments" element={<Experiments />} />
            <Route
              path="/benchmarks"
              element={<BenchmarkManager admin={admin} />}
            />
            <Route path="/analytics" element={<Analytics />} />
            <Route path="/memory" element={<MemoryCenter />} />
            <Route path="/models" element={<ModelRegistry />} />
            <Route path="/workflows" element={<WorkflowManager />} />
            <Route
              path="/settings"
              element={<SettingsPage theme={theme} setTheme={setTheme} />}
            />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </main>
      </div>
    </div>
  );
}
