import { useState } from 'react';
import type { NewAppointment, User } from '../types';

interface Props { providers: User[]; onSubmit: (form: NewAppointment) => Promise<void> }

const TYPES = ['Check-up', 'Follow-up', 'Consultation'];

export function AppointmentForm({ providers, onSubmit }: Props) {
  const [providerId, setProviderId] = useState('');
  const [when, setWhen] = useState('');
  const [type, setType] = useState(TYPES[0]);
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    setBusy(true);
    await onSubmit({
      providerId: providerId || providers[0]?.id || '',
      startTime: new Date(when).toISOString(),
      durationMin: 30, appointmentType: type, reason,
    });
    setBusy(false); setWhen(''); setReason('');
  };

  return (
    <section className="panel">
      <h2>Request an appointment</h2>
      <label>Provider
        <select value={providerId || providers[0]?.id || ''} onChange={(e) => setProviderId(e.target.value)}>
          {providers.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
      </label>
      <label>Preferred date and time
        <input type="datetime-local" value={when} onChange={(e) => setWhen(e.target.value)} />
      </label>
      <label>Visit type
        <select value={type} onChange={(e) => setType(e.target.value)}>
          {TYPES.map((t) => <option key={t}>{t}</option>)}
        </select>
      </label>
      <label>Reason for visit
        <textarea rows={3} value={reason} onChange={(e) => setReason(e.target.value)} />
      </label>
      <button className="primary" disabled={!when || !reason.trim() || busy} onClick={submit}>
        {busy ? 'Sending…' : 'Send request'}
      </button>
      <p className="hint">Requests start as pending until the provider confirms.</p>
    </section>
  );
}
