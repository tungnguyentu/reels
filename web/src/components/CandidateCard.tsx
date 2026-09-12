import { clock, thumbUrl, type Candidate, type Verdict } from "../api";

const VERDICT_STYLES: Record<string, string> = {
  accepted: "bg-emerald-500/15 text-emerald-300 ring-emerald-500/30",
  rejected: "bg-rose-500/15 text-rose-300 ring-rose-500/30",
  errored: "bg-amber-500/15 text-amber-300 ring-amber-500/30",
};

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
  // Three samples across the span: a range that drifts from wide to near-field looks
  // fine in any single frame.
  const times = [0.2, 0.5, 0.8].map((f) => candidate.start + f * candidate.duration);
  const key = verdict ? verdictKey(verdict) : null;

  return (
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={selected}
      className={`group flex flex-col gap-2 rounded-lg border p-2 text-left transition ${
        selected
          ? "border-emerald-400 bg-emerald-400/5"
          : "border-neutral-800 bg-neutral-900/60 hover:border-neutral-600"
      }`}
    >
      <div className="grid grid-cols-3 gap-1">
        {times.map((t) => (
          <img
            key={t}
            src={thumbUrl(video, t)}
            alt=""
            loading="lazy"
            className="aspect-video w-full rounded object-cover"
          />
        ))}
      </div>
      <div className="flex items-center justify-between text-xs">
        <span className="font-mono text-neutral-300">
          {clock(candidate.start)}–{clock(candidate.end)}
        </span>
        <span className="text-neutral-500">
          {candidate.duration.toFixed(0)}s · {candidate.peak.toFixed(3)}
        </span>
      </div>
      {key && (
        <div className={`rounded px-2 py-1 text-xs ring-1 ${VERDICT_STYLES[key]}`}>
          <span className="font-medium capitalize">{key}</span>
          {verdict?.reason && <span className="ml-1 opacity-80">— {verdict.reason}</span>}
        </div>
      )}
    </button>
  );
}
