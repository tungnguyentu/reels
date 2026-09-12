import { clock, thumbUrl, type Candidate, type Treatment, type Verdict } from "../api";
import { CropPreview } from "./CropPreview";

export function VariantPanel({
  video,
  candidate,
  verdict,
  tracks,
  treatments,
  onChange,
  onRender,
  busy,
}: {
  video: string;
  candidate: Candidate;
  verdict?: Verdict;
  tracks: string[];
  treatments: Treatment[];
  onChange: (t: Treatment[]) => void;
  onRender: () => void;
  busy: boolean;
}) {
  const mid = candidate.start + candidate.duration / 2;

  const update = (i: number, patch: Partial<Treatment>) =>
    onChange(treatments.map((t, j) => (j === i ? { ...t, ...patch } : t)));

  const add = () =>
    onChange([...treatments, { reframe: "crop", focal_x: verdict?.focal_x ?? 0.5, track: null }]);

  return (
    <aside className="flex w-96 shrink-0 flex-col gap-4 border-l border-neutral-800 bg-neutral-900/40 p-4">
      <header>
        <h2 className="text-sm font-medium text-neutral-200">
          {clock(candidate.start)}–{clock(candidate.end)}
        </h2>
        <p className="text-xs text-neutral-500">
          {treatments.length} variant{treatments.length === 1 ? "" : "s"} of this moment
        </p>
      </header>

      <div className="flex flex-col gap-4 overflow-y-auto">
        {treatments.map((t, i) => (
          <div key={i} className="rounded-lg border border-neutral-800 bg-neutral-950/60 p-3">
            <div className="mb-2 flex items-center justify-between">
              <div className="inline-flex overflow-hidden rounded border border-neutral-700">
                {(["crop", "pillarbox"] as const).map((mode) => (
                  <button
                    key={mode}
                    type="button"
                    onClick={() => update(i, { reframe: mode })}
                    className={`px-2 py-1 text-xs ${
                      t.reframe === mode
                        ? "bg-emerald-500/20 text-emerald-300"
                        : "text-neutral-400 hover:bg-neutral-800"
                    }`}
                  >
                    {mode}
                  </button>
                ))}
              </div>
              {treatments.length > 1 && (
                <button
                  type="button"
                  onClick={() => onChange(treatments.filter((_, j) => j !== i))}
                  className="text-xs text-neutral-500 hover:text-rose-400"
                >
                  remove
                </button>
              )}
            </div>

            <CropPreview
              src={thumbUrl(video, mid)}
              focalX={t.focal_x}
              pillarbox={t.reframe === "pillarbox"}
              onFocalChange={(x) => update(i, { focal_x: x })}
            />

            <label className="mt-2 block text-xs text-neutral-400">
              music
              <select
                value={t.track ?? ""}
                onChange={(e) => update(i, { track: e.target.value || null })}
                className="mt-1 w-full rounded border border-neutral-700 bg-neutral-900 px-2 py-1 text-neutral-200"
              >
                <option value="">auto</option>
                {tracks.map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              </select>
            </label>
          </div>
        ))}
      </div>

      <div className="mt-auto flex gap-2">
        <button
          type="button"
          onClick={add}
          disabled={treatments.length >= 8}
          className="rounded border border-neutral-700 px-3 py-2 text-sm text-neutral-300 hover:bg-neutral-800 disabled:opacity-40"
        >
          + variant
        </button>
        <button
          type="button"
          onClick={onRender}
          disabled={busy || treatments.length === 0}
          className="flex-1 rounded bg-emerald-500 px-3 py-2 text-sm font-medium text-neutral-950 hover:bg-emerald-400 disabled:opacity-40"
        >
          {busy ? "rendering…" : `render ${treatments.length}`}
        </button>
      </div>
    </aside>
  );
}
