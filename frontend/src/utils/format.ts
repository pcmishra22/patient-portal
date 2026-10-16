export const formatDate = (iso: string) =>
  new Date(iso).toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' });

export const formatTime = (iso: string) =>
  new Date(iso).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });

export const formatStamp = (iso: string) => `${formatDate(iso)}, ${formatTime(iso)}`;

/** ISO (UTC) -> value for <input type="datetime-local"> in the viewer's local time. */
export const toLocalInput = (iso: string) => {
  const d = new Date(iso);
  d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
  return d.toISOString().slice(0, 16);
};
