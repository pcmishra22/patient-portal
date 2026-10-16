import { useState } from 'react';
import { AuditLog } from '../components/AuditLog';
import { NotificationBell } from '../components/NotificationBell';
import { AppointmentForm } from '../components/AppointmentForm';
import { AppointmentList } from '../components/AppointmentList';
import { ConflictAlert } from '../components/ConflictAlert';
import { RescheduleDialog } from '../components/RescheduleDialog';
import { RoleSwitcher } from '../components/RoleSwitcher';
import { useAppointments } from '../hooks/useAppointments';
import { useNotifications } from '../hooks/useNotifications';
import { useUsers } from '../hooks/useUsers';
import type { Appointment } from '../types';

/** Composition only: data comes from hooks, rules live on the server. */
export function PortalPage() {
  const { users, user, setUser, error } = useUsers();
  const { appointments, notice, busyId, dismissNotice, create, confirm, cancel, reschedule } = useAppointments(user);
  const inbox = useNotifications(user);
  const [view, setView] = useState<'appointments' | 'audit'>('appointments');
  const [rescheduling, setRescheduling] = useState<Appointment | null>(null);

  const message = notice ?? error;
  return (
    <div className="shell">
      <header>
        <h1>Patient portal</h1>
        {user && (
          <div className="row">
            <NotificationBell items={inbox.items} unread={inbox.unread}
              onMarkAllRead={() => void inbox.markAllRead()} onMarkRead={(id) => void inbox.markRead(id)} />
            <RoleSwitcher users={users} user={user} onChange={setUser} />
          </div>
        )}
      </header>

      {user && (
        <nav className="tabs" aria-label="Sections">
          <button className={view === 'appointments' ? 'on' : ''} aria-pressed={view === 'appointments'}
            onClick={() => setView('appointments')}>Appointments</button>
          <button className={view === 'audit' ? 'on' : ''} aria-pressed={view === 'audit'}
            onClick={() => setView('audit')}>Audit log</button>
        </nav>
      )}

      {message && <ConflictAlert message={message} onClose={dismissNotice} />}

      {user && view === 'audit' && <AuditLog user={user} />}

      {user && view === 'appointments' && (
        <main className={user.role === 'patient' ? 'two' : ''}>
          <AppointmentList user={user} appointments={appointments} busyId={busyId}
            onConfirm={confirm} onCancel={cancel} onReschedule={setRescheduling} />
          {user.role === 'patient' && (
            <AppointmentForm providers={users.filter((u) => u.role === 'provider')} onSubmit={create} />
          )}
        </main>
      )}

      {rescheduling && (
        <RescheduleDialog appointment={rescheduling} onClose={() => setRescheduling(null)}
          onSave={(iso) => { const a = rescheduling; setRescheduling(null); void reschedule(a, iso); }} />
      )}
    </div>
  );
}
