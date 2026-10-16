import { useHistory } from '../hooks/useHistory';
import type { Appointment, User } from '../types';
import { formatStamp } from '../utils/format';

interface Props { user: User; appointment: Appointment }

export function AppointmentHistory({ user, appointment }: Props) {
  const events = useHistory(user, appointment.id, appointment.version);
  if (!events) return <p className="hint">Loading history…</p>;
  return (
    <ol className="history">
      {events.map((e) => (
        <li key={e.id}>
          <b>{e.action}</b> by {e.actorName} ({e.actorRole}) on {formatStamp(e.createdAt)}
          <div className="hint">
            {e.fromStatus !== e.toStatus && <>Status: {e.fromStatus ?? 'none'} to {e.toStatus}. </>}
            {e.fromStart !== e.toStart && <>Time: {e.fromStart ? formatStamp(e.fromStart) : 'none'} to {formatStamp(e.toStart)}.</>}
          </div>
        </li>
      ))}
    </ol>
  );
}
