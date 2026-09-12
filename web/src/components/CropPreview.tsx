import { useState } from "react";

/**
 * A thumbnail with the 9:16 crop window drawn on it.
 *
 * The point of the slider is that you see where the crop lands instead of trusting a
 * number. Window width comes from the image's own aspect ratio, so it is correct for
 * any source without asking the server for dimensions.
 */
export function CropPreview({
  src,
  focalX,
  pillarbox,
  onFocalChange,
}: {
  src: string;
  focalX: number;
  pillarbox: boolean;
  onFocalChange?: (x: number) => void;
}) {
  const [ratio, setRatio] = useState<number | null>(null);

  // Fraction of the source width a 9:16 window covers.
  const windowFraction = ratio ? Math.min(1, 9 / 16 / ratio) : 0.25;
  const left = Math.max(0, Math.min(1 - windowFraction, focalX - windowFraction / 2));

  return (
    <div className="relative select-none overflow-hidden rounded-md bg-neutral-900">
      <img
        src={src}
        alt=""
        loading="lazy"
        draggable={false}
        onLoad={(e) => {
          const img = e.currentTarget;
          if (img.naturalHeight) setRatio(img.naturalWidth / img.naturalHeight);
        }}
        className="block w-full"
      />
      {pillarbox ? (
        <div className="absolute inset-0 ring-2 ring-inset ring-sky-400/80" />
      ) : (
        <>
          <div className="absolute inset-y-0 left-0 bg-black/65" style={{ width: `${left * 100}%` }} />
          <div
            className="absolute inset-y-0 right-0 bg-black/65"
            style={{ width: `${(1 - left - windowFraction) * 100}%` }}
          />
          <div
            className="absolute inset-y-0 ring-2 ring-inset ring-emerald-400"
            style={{ left: `${left * 100}%`, width: `${windowFraction * 100}%` }}
          />
        </>
      )}
      {onFocalChange && !pillarbox && (
        <input
          type="range"
          min={0}
          max={1}
          step={0.01}
          value={focalX}
          onChange={(e) => onFocalChange(Number(e.target.value))}
          aria-label="Crop focal point"
          className="absolute inset-x-0 bottom-1 mx-2 accent-emerald-400"
        />
      )}
    </div>
  );
}
