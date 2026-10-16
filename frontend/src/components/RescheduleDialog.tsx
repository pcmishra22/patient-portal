import { useState } from 'react';
import type { Appointment } from '../types';
import { formatStamp, toLocalInput } from '../utils/format';

interface Props { appointment: Appointment; onSave: (startIso: string) => void; onClose: () => void }

export function RescheduleDialog({ appointment, onSave, onClose }: Props) {
  const [when, setWhen] = useState(toLocalInput(appointment.startTime));
  return (
    <div className="scrim" onClick={onClose}>
      <div className="dialog" role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
        <h2>Reschedule {appointment.patientName}</h2>
        <p className="hint">Currently {formatStamp(appointment.startTime)}</p>
        <label>New date and time
          <input type="datetime-local" value={when} onChange={(e) => setWhen(e.target.value)} />
        </label>
        <div className="row">
          <button onClick={onClose}>Keep current time</button>
          <button className="primary" disabled={!when} onClick={() => onSave(new Date(when).toISOString())}>Save new time</button>
        </div>
      </div>
    </div>
  );
}
