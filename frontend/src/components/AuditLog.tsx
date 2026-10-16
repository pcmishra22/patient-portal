import { useState } from 'react';
import { useAuditLog } from '../hooks/useAuditLog';
import type { Action, AuditEntry, User } from '../types';
import { formatStamp } from '../utils/format';

interface Props { user: User }

const ACTIONS: Action[] = ['created', 'confirmed', 'rescheduled', 'cancelled'];

function change(e: AuditEntry): string {
  const parts: string[] = [];
  if (e.fromStatus !== e.toStatus) parts.push(`Status: ${e.fromStatus ?? 'none'} → ${e.toStatus}`);
  if (e.fromStart !== e.toStart) {
    parts.push(`Time: ${e.fromStart ? formatStamp(e.fromStart) : 'none'} → ${formatStamp(e.toStart)}`);
  }
  return parts.join(' · ') || '—';
}

/** Read-only, append-only trail of every change: when, who, what, and the before/after values. */
export function AuditLog({ user }: Props) {
  const [filter, setFilter] = useState<Action | ''>('');
  const { entries, error, refresh } = useAuditLog(user, filter);

  return (
    <section className="panel" aria-label="Audit log">
      <div className="row between">
        <h2>Audit log</h2>
        <div className="row">
          <label className="inline">Action
            <select value={filter} onChange={(e) => setFilter(e.target.value as Action | '')}>
              <option value="">All</option>
              {ACTIONS.map((a) => <option key={a} value={a}>{a}</option>)}
            </select>
          </label>
          <button onClick={() => void refresh()}>Refresh</button>
        </div>
      </div>
      <p className="hint">Every create, confirm, reschedule and cancel is recorded with who did it and the before/after values. Entries cannot be edited or deleted.</p>
      {error && <p className="hint">Could not load the audit log.</p>}
      {!entries && !error && <p className="hint">Loading…</p>}
      {entries && entries.length === 0 && !error && <p className="hint">No entries yet.</p>}
      {entries && entries.length > 0 && (
        <div className="table-wrap">
          <table className="audit">
            <thead><tr><th>When</th><th>Appointment</th><th>Action</th><th>By</th><th>What changed</th></tr></thead>
            <tbody>
              {entries.map((e) => (
                <tr key={e.id}>
                  <td>{formatStamp(e.createdAt)}</td>
                  <td>#{e.appointmentId} {e.appointmentType}<div className="hint">{e.patientName} with {e.providerName}</div></td>
                  <td><span className={`badge ${badgeFor(e.action)}`}>{e.action}</span></td>
                  <td>{e.actorName}<div className="hint">{e.actorRole}</div></td>
                  <td>{change(e)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

const badgeFor = (a: Action) => (a === 'created' ? 'pending' : a === 'cancelled' ? 'cancelled' : 'confirmed');
