from pathlib import Path
p=Path("frontend/src/app/(console)/page.tsx");s=p.read_text()
s=s.replace('''                player.flush();
                controller.abort();''','''                player.flush();
                if (abortRef.current === controller) abortRef.current = null;
                controller.abort();
                await reconcileSession(sid);''',1)
old='''      await api.stopChat(sid);
      if (pendingPreparation) await reconcileSession(sid);
      if (runSidRef.current !== sid) return;
      playerRef.current?.flush();
      abortRef.current?.abort();
      setMessages((prev) => prev.map((m) => m.id === asstIdRef.current
        ? { ...m, streaming: false, stopped: true, status: undefined } : m));
      setSending(false);
      void refreshSessions();'''
new='''      await api.stopChat(sid);
      if (runSidRef.current === sid) {
        playerRef.current?.flush();
        const controller = abortRef.current;
        abortRef.current = null;
        controller?.abort();
        setSending(false);
      }
      // Stop can race a completed answer; persisted status decides the label.
      await reconcileSession(sid);
      void refreshSessions();'''
assert old in s;s=s.replace(old,new)
p.write_text(s)
