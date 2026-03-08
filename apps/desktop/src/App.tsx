import { useEffect, type ReactNode } from "react";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { AuthProvider } from "./context/AuthContext";
import Layout from "./components/Layout";
import ProtectedRoute from "./components/ProtectedRoute";
import Dashboard from "./pages/Dashboard";
import Profile from "./pages/Profile";
import Chat from "./pages/Chat";
import Memory from "./pages/Memory";
import Settings from "./pages/Settings";
import Soul from "./pages/Soul";
import Tasks from "./pages/Tasks";
import Login from "./pages/Login";
import Register from "./pages/Register";
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

  return (
    <Routes>
      <Route path="/" element={withLayout(<Dashboard />)} />
      <Route path="/chat" element={withLayout(<Chat />)} />
      <Route path="/memory" element={withLayout(<Memory />)} />
      <Route path="/profile" element={withLayout(<Profile />)} />
      <Route path="/settings" element={withLayout(<Settings />)} />
      <Route path="/tasks" element={withLayout(<Tasks />)} />
      <Route path="/soul" element={withLayout(<Soul />)} />
      <Route path="/login" element={<Login />} />
      <Route path="/register" element={<Register />} />
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
