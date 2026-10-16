import { useCallback, useEffect, useRef, useState } from 'react';
import * as api from '../api';
import type { NotificationItem, User } from '../types';

const POLL_MS = 5000;

/** In-app inbox for the acting user. Polls (simple; use SSE/websocket at scale). It deliberately does NOT reload
 *  the appointment list: a stale screen is handled by the version check (409), not by silently changing the view. */
export function useNotifications(user: User | null) {
  const [items, setItems] = useState<NotificationItem[]>([]);
  const latest = useRef(0); // ignore out-of-order responses after switching users

  const load = useCallback(async () => {
    if (!user) return;
    const mine = ++latest.current;
    try {
      const list = await api.listNotifications(user);
      if (mine === latest.current) setItems(list);
    } catch { /* keep the last known list; the main list shows connection errors */ }
  }, [user]);

  useEffect(() => {
    setItems([]);
    void load();
    const timer = setInterval(() => void load(), POLL_MS);
    return () => clearInterval(timer);
  }, [load]);

  const markAllRead = useCallback(async () => {
    if (!user) return;
    try { await api.markAllNotificationsRead(user); } catch { /* the reload below shows what actually happened */ }
    await load();
  }, [user, load]);

  const markRead = useCallback(async (id: number) => {
    if (!user) return;
    try { await api.markNotificationRead(user, id); } catch { /* the reload below shows what actually happened */ }
    await load();
  }, [user, load]);

  return { items, unread: items.filter((n) => !n.readAt).length, markAllRead, markRead };
}
