import { useCallback, useEffect, useRef, useState } from 'react';
import { getAuditLog } from '../api';
import type { Action, AuditEntry, User } from '../types';

/** Global audit log for the acting user, optionally filtered by action. `refresh` re-queries on demand. */
export function useAuditLog(user: User, action: Action | '') {
  const [entries, setEntries] = useState<AuditEntry[] | null>(null);
  const [error, setError] = useState(false);
  const latest = useRef(0);

  const refresh = useCallback(async () => {
    const mine = ++latest.current;
    setError(false);
    try {
      const list = await getAuditLog(user, action || undefined);
      if (mine === latest.current) setEntries(list);
    } catch { if (mine === latest.current) { setEntries([]); setError(true); } }
  }, [user, action]);

  useEffect(() => { setEntries(null); void refresh(); }, [refresh]);

  return { entries, error, refresh };
}
