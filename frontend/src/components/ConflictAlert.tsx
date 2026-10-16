interface Props { message: string; onClose: () => void }

/** Explains stale updates, unavailable time slots and other errors. */
export function ConflictAlert({ message, onClose }: Props) {
  return (
    <div className="alert" role="alert">
      <span>{message}</span>
      <button className="link" onClick={onClose}>Dismiss</button>
    </div>
  );
}
