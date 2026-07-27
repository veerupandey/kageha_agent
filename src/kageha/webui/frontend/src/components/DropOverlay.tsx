interface DropOverlayProps {
  visible: boolean;
}

export function DropOverlay({ visible }: DropOverlayProps) {
  return (
    <div
      className={`drop-overlay${visible ? " visible" : ""}`}
      id="drop-overlay"
      hidden={!visible}
    >
      <p>Drop files to attach</p>
    </div>
  );
}
