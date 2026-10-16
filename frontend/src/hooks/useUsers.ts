import { useEffect, useState } from 'react';
import { listUsers } from '../api';
import type { User } from '../types';

/** Loads the demo users and tracks which one is "logged in" via the role switcher. */
export function useUsers() {
  const [users, setUsers] = useState<User[]>([]);
  const [user, setUser] = useState<User | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listUsers()
      .then((list) => { setUsers(list); setUser(list.find((u) => u.role === 'patient') ?? list[0] ?? null); })
      .catch(() => setError('Could not reach the server. Is the backend running on port 8000?'));
  }, []);

  return { users, user, setUser, error };
}
