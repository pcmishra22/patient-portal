import { useState } from 'react';
import type { Appointment, User } from '../types';
import { formatDate, formatTime } from '../utils/format';
import { AppointmentHistory } from './AppointmentHistory';
import { StatusBadge } from './StatusBadge';

interface Props {
  appointment: Appointment;
  user: User;
  busy: boolean;
  onConfirm: () => void;
  onCancel: () => void;
  onReschedule: () => void;
}

/** Buttons shown here are UX only: the server enforces who may do what. */
export function AppointmentCard({ appointment: a, user, busy, onConfirm, onCancel, onReschedule }: Props) {
  const [showHistory, setShowHistory] = useState(false);
  const isPatient = user.role === 'patient';
  return (
    <article className={`card ${a.status}`}>
      <div className="when"><b>{formatTime(a.startTime)}</b><span>{formatDate(a.startTime)}</span></div>
      <div className="main">
        <h3>{a.appointmentType} <StatusBadge status={a.status} /></h3>
        <p>{isPatient ? a.providerName : a.patientName} · until {formatTime(a.endTime)}</p>
        <p className="hint">{a.reason}</p>
        <div className="row">
          {isPatient && a.status === 'confirmed' && <button disabled={busy} onClick={onCancel}>Cancel appointment</button>}
          {!isPatient && a.status === 'pending' && <button className="primary" disabled={busy} onClick={onConfirm}>Confirm</button>}
          {!isPatient && a.status !== 'cancelled' && <button disabled={busy} onClick={onReschedule}>Reschedule</button>}
          <button className="link" onClick={() => setShowHistory((v) => !v)}>{showHistory ? 'Hide history' : 'View history'}</button>
        </div>
        {showHistory && <AppointmentHistory user={user} appointment={a} />}
      </div>
    </article>
  );
}
