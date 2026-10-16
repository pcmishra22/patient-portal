import { useEffect, useRef, useState } from 'react';
import type { NotificationItem } from '../types';
import { formatStamp } from '../utils/format';

/** Patients/providers don't need worker internals: only whether the message reached them. */
const deliveryLabel = (n: NotificationItem) =>
  n.deliveryStatus === 'sent' ? 'sent' : n.gaveUp ? 'could not be sent' : 'sending…';

interface Props {
  items: NotificationItem[];
  unread: number;
  onMarkAllRead: () => void;
  onMarkRead: (id: number) => void;
}

/** Bell with unread count + dropdown inbox. Delivery status shows the stubbed email/SMS outcome. */
export function NotificationBell({ items, unread, onMarkAllRead, onMarkRead }: Props) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', close);
    document.addEventListener('keydown', esc);
    return () => { document.removeEventListener('mousedown', close); document.removeEventListener('keydown', esc); };
  }, [open]);

  return (
    <div className="bell" ref={box}>
      <button className="bell-btn" aria-haspopup="true" aria-expanded={open}
        aria-label={`Notifications, ${unread} unread`} onClick={() => setOpen((v) => !v)}>
        <span aria-hidden="true">🔔</span>
        {unread > 0 && <span className="count">{unread > 9 ? '9+' : unread}</span>}
      </button>
      {open && (
        <div className="bell-panel" role="region" aria-label="Notifications">
          <div className="row between">
            <b>Notifications</b>
            <button className="link" disabled={unread === 0} onClick={onMarkAllRead}>Mark all read</button>
          </div>
          {items.length === 0 && <p className="hint">Nothing yet. You will be notified when an appointment changes.</p>}
          <ul>
            {items.map((n) => (
              <li key={n.id} className={n.readAt ? 'read' : 'unread'}>
                <div className="row between">
                  <b>{n.subject}</b>
                  <span className="hint">{formatStamp(n.createdAt)}</span>
                </div>
                <p>{n.body}</p>
                <div className="row between">
                  <span className={`hint delivery ${n.gaveUp ? 'failed' : n.deliveryStatus === 'sent' ? 'sent' : 'pending'}`}>{n.channel}: {deliveryLabel(n)}</span>
                  {!n.readAt && <button className="link" onClick={() => onMarkRead(n.id)}>Mark read</button>}
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
