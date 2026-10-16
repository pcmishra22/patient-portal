import type { User } from '../types';

interface Props { users: User[]; user: User; onChange: (u: User) => void }

export function RoleSwitcher({ users, user, onChange }: Props) {
  return (
    <div className="switcher" role="group" aria-label="Demo user">
      {users.map((u) => (
        <button key={u.id} className={u.id === user.id ? 'on' : ''} aria-pressed={u.id === user.id} onClick={() => onChange(u)}>
          {u.name}<small>{u.role}</small>
        </button>
      ))}
    </div>
  );
}
