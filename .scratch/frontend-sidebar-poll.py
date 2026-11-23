from pathlib import Path
p=Path("frontend/src/lib/hooks/useSessions.ts");s=p.read_text();anchor='  // 初始化：拉会话列表'
s=s.replace(anchor,'''  const hasGeneratingSession = sessions.some((session) => session.generating);
  useEffect(() => {
    if (!hasGeneratingSession) return;
    let alive = true;
    let busy = false;
    const timer = window.setInterval(() => {
      if (busy) return;
      busy = true;
      void api.listSessions().then(({ sessions: list }) => {
        if (alive) setSessions(list);
      }).catch(() => {
        // Sidebar status retries quietly while a background task remains active.
      }).finally(() => { busy = false; });
    }, 5000);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [hasGeneratingSession]);

'''+anchor,1);p.write_text(s)
