import { useRef, useState } from "react";
import { clock, previewUrl, thumbUrl, type Candidate, type Verdict } from "../api";

const VERDICT_STYLES: Record<string, string> = {
  accepted: "bg-emerald-500/15 text-emerald-300 ring-emerald-500/30",
  rejected: "bg-rose-500/15 text-rose-300 ring-rose-500/30",
  errored: "bg-amber-500/15 text-amber-300 ring-amber-500/30",
};

const SCRUB_FRAMES = 8;

function verdictKey(v: Verdict) {
  return v.accepted === null ? "errored" : v.accepted ? "accepted" : "rejected";
}

export function CandidateCard({
  video,
  candidate,
  verdict,
  selected,
  onSelect,
}: {
  video: string;
  candidate: Candidate;
  verdict?: Verdict;
  selected: boolean;
  onSelect: () => void;
}) {
  const [scrub, setScrub] = useState<number | null>(null);
  const [playing, setPlaying] = useState(false);
  const strip = useRef<HTMLDivElement>(null);

  // Scanning twenty candidates should not need twenty clicks: hovering across the strip
  // scrubs the span, which is enough to reject a menu screen at a glance.
  const scrubTimes = Array.from({ length: SCRUB_FRAMES }, (_, i) =>
    candidate.start + ((i + 0.5) / SCRUB_FRAMES) * candidate.duration,
  );
  const stillTimes = [0.2, 0.5, 0.8].map((f) => candidate.start + f * candidate.duration);
  const key = verdict ? verdictKey(verdict) : null;

  const onMove = (e: React.MouseEvent) => {
    const box = strip.current?.getBoundingClientRect();
    if (!box) return;
    const fraction = Math.min(0.999, Math.max(0, (e.clientX - box.left) / box.width));
    setScrub(Math.floor(fraction * SCRUB_FRAMES));
  };

  return (
    <div
      className={`flex flex-col gap-2 rounded-lg border p-2 transition ${
        selected
          ? "border-emerald-400 bg-emerald-400/5"
          : "border-neutral-800 bg-neutral-900/60 hover:border-neutral-600"
      }`}
    >
      <div
        ref={strip}
        onMouseMove={playing ? undefined : onMove}
        onMouseLeave={() => setScrub(null)}
        className="relative cursor-pointer"
        onClick={onSelect}
      >
        {playing ? (
          <video
            src={previewUrl(video, candidate.start, candidate.end)}
            autoPlay
            loop
            muted
            controls
            onClick={(e) => e.stopPropagation()}
            className="aspect-video w-full rounded bg-black"
          />
        ) : scrub === null ? (
          <div className="grid grid-cols-3 gap-1">
            {stillTimes.map((t) => (
              <img
                key={t}
                src={thumbUrl(video, t)}
                alt=""
                loading="lazy"
                className="aspect-video w-full rounded object-cover"
              />
            ))}
          </div>
        ) : (
          <>
            <img
              src={thumbUrl(video, scrubTimes[scrub])}
              alt=""
              className="aspect-video w-full rounded object-cover"
            />
            <div className="absolute inset-x-0 bottom-0 flex gap-0.5 px-1 pb-1">
              {scrubTimes.map((_, i) => (
                <span
                  key={i}
                  className={`h-0.5 flex-1 rounded ${i === scrub ? "bg-emerald-400" : "bg-white/25"}`}
                />
              ))}
            </div>
          </>
        )}

        {/* Preload the scrub frames once, so hovering is instant rather than a flicker. */}
        {scrub !== null &&
          scrubTimes.map((t) => <link key={t} rel="prefetch" href={thumbUrl(video, t)} />)}
      </div>

      <div className="flex items-center justify-between text-xs">
        <span className="font-mono text-neutral-300">
          {clock(candidate.start)}–{clock(candidate.end)}
        </span>
        <div className="flex items-center gap-2">
          <span className="text-neutral-500">
            {candidate.duration.toFixed(0)}s · {candidate.peak.toFixed(3)}
          </span>
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              setPlaying((p) => !p);
              setScrub(null);
            }}
            className="rounded border border-neutral-700 px-1.5 py-0.5 text-neutral-300 hover:bg-neutral-800"
          >
            {playing ? "stills" : "▶ watch"}
          </button>
          <button
            type="button"
            onClick={onSelect}
            className={`rounded px-1.5 py-0.5 ${
              selected
                ? "bg-emerald-500 text-neutral-950"
                : "border border-neutral-700 text-neutral-300 hover:bg-neutral-800"
            }`}
          >
            {selected ? "chosen" : "choose"}
          </button>
        </div>
      </div>

      {key && (
        <div className={`rounded px-2 py-1 text-xs ring-1 ${VERDICT_STYLES[key]}`}>
          <span className="font-medium capitalize">{key}</span>
          {verdict?.reason && <span className="ml-1 opacity-80">— {verdict.reason}</span>}
        </div>
      )}
    </div>
  );
}
