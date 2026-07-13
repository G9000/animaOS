import { useEffect, type ReactNode } from "react";
import { BrowserRouter, Routes, Route, Navigate, Link } from "react-router-dom";
import { AuthProvider } from "./context/AuthContext";
import Layout from "./components/Layout";
import ProtectedRoute from "./components/ProtectedRoute";
import Dashboard from "./pages/dashboard/Dashboard";
import Profile from "./pages/Profile";
import Chat from "./pages/chat/Chat";
import Memory from "./pages/memory/Memory";
import KnowledgeLibrary from "./pages/knowledge/KnowledgeLibrary";
import MemoryImages from "./pages/memory/MemoryImages";
import Presence from "./pages/Presence";
import Settings from "./pages/settings/Settings";
import AiSettings from "./pages/settings/AiSettings";
import SecuritySettings from "./pages/settings/SecuritySettings";
import VaultSettings from "./pages/settings/VaultSettings";
import AdvancedSettings from "./pages/settings/AdvancedSettings";
import AppearanceSettings from "./pages/settings/AppearanceSettings";
import LanguageSettings from "./pages/settings/LanguageSettings";
import DaemonSettings from "./pages/settings/DaemonSettings";
import AgentProfileSettings from "./pages/agent-customization/AgentCustomization";
import Soul from "./pages/Soul";
import Consciousness from "./pages/Consciousness";
import Tasks from "./pages/Tasks";
import Database from "./pages/Database";
import KnowledgeGraph from "./pages/memory/KnowledgeGraph";
import Mods from "./pages/Mods";
import ModDetail from "./pages/ModDetail";
import Journal from "./pages/Journal";
import Login from "./pages/auth/Login";
import Register from "./pages/auth/Register";
import Init from "./pages/init/Init";
import { AtmosphereShell } from "./components/AtmosphereShell";
import "./index.css";

// Register global shortcut to summon ANIMA (Cmd+Shift+A / Ctrl+Shift+A)
function useGlobalShortcut() {
  useEffect(() => {
    let cleanup: (() => void) | null = null;

    (async () => {
      try {
        const { register, unregister } =
          await import("@tauri-apps/plugin-global-shortcut");
        const { getCurrentWindow } = await import("@tauri-apps/api/window");

        await register("CommandOrControl+Shift+A", async () => {
          const win = getCurrentWindow();
          await win.show();
          await win.setFocus();
        });

        cleanup = () => {
          unregister("CommandOrControl+Shift+A").catch(() => {});
        };
      } catch {
        // Not running in Tauri — skip
      }
    })();

    return () => {
      cleanup?.();
    };
  }, []);
}

function AppRoutes() {
  const withLayout = (page: ReactNode) => (
    <ProtectedRoute>
      <Layout>{page}</Layout>
    </ProtectedRoute>
  );

  const daemonRecovery = (
    <div className="min-h-screen w-screen bg-background text-foreground overflow-auto p-4 sm:p-8">
      <div className="max-w-5xl mx-auto space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="font-mono text-[10px] uppercase tracking-[0.24em] text-foreground/40">
              Daemon recovery
            </p>
            <h1 className="text-2xl font-light tracking-[-0.03em]">
              Runtime access controls
            </h1>
          </div>
          <div className="flex gap-2">
            <Link
              className="border border-foreground/15 px-3 py-2 rounded-sm font-mono text-[10px] uppercase tracking-[0.16em] hover:bg-foreground/8"
              to="/login"
            >
              Login
            </Link>
            <Link
              className="border border-foreground/15 px-3 py-2 rounded-sm font-mono text-[10px] uppercase tracking-[0.16em] hover:bg-foreground/8"
              to="/settings/daemon"
            >
              Settings
            </Link>
          </div>
        </div>
        <DaemonSettings recoveryMode />
      </div>
    </div>
  );

  return (
    <Routes>
      <Route path="/" element={withLayout(<Dashboard />)} />
      <Route path="/chat" element={withLayout(<Chat />)} />
      <Route path="/memory" element={withLayout(<Memory />)} />
      <Route path="/knowledge" element={withLayout(<KnowledgeLibrary />)} />
      <Route path="/memory/images" element={withLayout(<MemoryImages />)} />
      <Route path="/presence" element={withLayout(<Presence />)} />
      <Route path="/profile" element={withLayout(<Profile />)} />
      <Route path="/settings" element={withLayout(<Settings />)}>
        <Route index element={<Navigate to="ai" replace />} />
        <Route path="ai" element={<AiSettings />} />
        <Route path="security" element={<SecuritySettings />} />
        <Route path="vault" element={<VaultSettings />} />
        <Route path="language" element={<LanguageSettings />} />
        <Route path="appearance" element={<AppearanceSettings />} />
        <Route path="daemon" element={<DaemonSettings />} />
        <Route path="advanced" element={<AdvancedSettings />} />
      </Route>
      <Route path="/agent" element={withLayout(<AgentProfileSettings />)} />
      <Route path="/tasks" element={withLayout(<Tasks />)} />
      <Route path="/soul" element={withLayout(<Soul />)} />
      <Route path="/consciousness" element={withLayout(<Consciousness />)} />
      <Route path="/database" element={withLayout(<Database />)} />
      <Route path="/graph" element={withLayout(<KnowledgeGraph />)} />
      <Route path="/daemon" element={daemonRecovery} />
      <Route element={<AtmosphereShell />}>
        <Route path="/login" element={<Login />} />
        <Route path="/register" element={<Register />} />
        <Route path="/init" element={<Init />} />
      </Route>
      <Route path="/journal" element={withLayout(<Journal />)} />
      <Route path="/mods" element={withLayout(<Mods />)} />
      <Route path="/mods/:id" element={withLayout(<ModDetail />)} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

function App() {
  useGlobalShortcut();

  return (
    <BrowserRouter>
      <AuthProvider>
        <AppRoutes />
      </AuthProvider>
    </BrowserRouter>
  );
}

export default App;
