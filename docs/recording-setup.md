# Recording Setup

This document lists recording-side settings that make future sessions easier to reframe into 9:16.

None of it is required. The tool works on the existing library and on anything recorded after reading this. Nothing here is checked, detected, or enforced in code. It is creator-applied setup.

Every number below comes from one reference recording: 3410x1372 (2.49:1), 60 fps, h264, 12.36 Mbit/s, 75 minutes, 6.9 GB. Your own capture profile is what matters; these are here so you can see which setting each change is reacting to.

## Hide the HUD

Press F1 before recording. Minecraft toggles the HUD with F1.

The HUD — hearts, hunger, XP bar, hotbar — sits across the bottom centre of the frame. A 9:16 centre crop of a 2.49:1 source keeps about 22% of the frame width. That slices the hotbar off at both edges. A partial hotbar reads as broken, not as deliberate.

Hiding the HUD removes the problem instead of working around it. This is the highest-value change on the list.

The tradeoff is real: you cannot see your own health, hunger, or hotbar while recording. That suits scenery passes, build tours, and exploration. It does not suit combat or survival play where you need to read your own state.

## Field of view

Raise the FOV in the video settings slider when shooting for reframing.

A first-person FOV tuned for play puts the camera close to whatever is in front of it. A prompt like "forest landscape" then matches a frame filled with leaves two blocks away. The match is correct; the framing is not what you wanted.

A wider FOV gets more of the scene into the frame and gives the 9:16 crop more to keep.

FOV alone does not fix it. Standing back from the subject does. Walk away from the trees before you pan across them. Put distance between the camera and the build. Treat this as a shooting habit, and the FOV slider as the smaller half of it.

## Capture aspect ratio

Record a narrower window than 2.49:1.

The 2.49:1 capture is the root cause of the crop problem. The wider the source, the smaller the fraction of it a vertical crop can keep. At 2.49:1 that fraction is about 22% of the width. At 16:9 it is far more, and everything narrower than 16:9 keeps more still.

Two options:

- Record a 16:9 window, or narrower. Play windowed at that size, or set the capture region to it. This keeps the footage watchable on a monitor while making the vertical crop far less destructive.
- Record vertically. If you know a session is destined for Reels, capture at 9:16 outright. There is then no crop at all.

The tradeoff: a narrower capture is worse to watch back on a monitor. Vertical capture is worse still. Pick per session, based on where the footage is going.

## Play at full health

Play at full health when recording.

The reference recording has a red low-health vignette at the frame edges in every frame sampled across the full 75 minutes. The player was at low health for the entire session. Every clip cut from that recording ships with red-tinted edges.

The tool does not correct this and will not. There is no post step that removes it.

This is not a settings change. It is a play-state one. Eat, heal, and then record.

## Keyframes

No action needed here, but it is worth knowing what the tool depends on.

The reference recording has keyframes every 2.0 seconds, uniformly. The tool reads those existing keyframes instead of decoding the video, which is why indexing a 75-minute file takes 54 seconds rather than around 5 minutes.

Any recorder setting that lengthens the keyframe interval makes indexing slower. It does not make it wrong. Results are the same; the wait is longer.

## The heavier option: Replay Mod

Minecraft's Replay Mod records world state rather than pixels. You re-render afterwards along any camera path you choose, including a native vertical one, at any FOV, with or without the HUD.

That sidesteps the crop problem entirely for footage you know in advance is destined for Reels. You are no longer cropping a wide frame; you are rendering the frame you want.

It is more setup and a render step after the session. It is listed here because it is worth knowing about, not as a recommendation.

## None of this is required

The tool runs on recordings made under the old configuration and the new one. The new configuration improves output quality. It is not a precondition.

The tool does not detect which configuration a recording was made under, does not warn about the old one, and does not enforce any of it. These are habits you apply at the recorder, not rules the code checks.
