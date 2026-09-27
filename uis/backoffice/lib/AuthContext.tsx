"use client";

import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { clearToken, getToken } from "./auth-storage";
import { me as fetchMe, type MeRead } from "./auth-api";
import { setTelemetryUser } from "./telemetry";

interface AuthContextValue {
  user: MeRead | null;
  loading: boolean;
  setUser: (user: MeRead | null) => void;
  logout: () => void;
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUserState] = useState<MeRead | null>(null);
  const [loading, setLoading] = useState(true);

  const setUser = useCallback((nextUser: MeRead | null) => {
    setUserState(nextUser);
    setTelemetryUser(nextUser?.profile.user_id ?? null);
  }, []);

  useEffect(() => {
    const token = getToken();
    if (!token) {
      setLoading(false);
      return;
    }

    fetchMe()
      .then(setUser)
      .catch(() => setUser(null))
      .finally(() => setLoading(false));
  }, [setUser]);

  const logout = useCallback(() => {
    clearToken();
    setUser(null);
    if (typeof window !== "undefined") {
      window.location.href = "/login";
    }
  }, [setUser]);

  return (
    <AuthContext.Provider value={{ user, loading, setUser, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth debe usarse dentro de <AuthProvider>");
  }
  return context;
}
