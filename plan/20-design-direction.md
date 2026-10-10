# Design direction

- Status: Adopted for the interface on 2026-10-10 at the owner's request; open to the owner's
  reaction once seen in use.
- Related: ADR-017, [`19-assistant-first-experience.md`](19-assistant-first-experience.md),
  `frontend/src/styles/`.

## The problem

The first redesign (2026-10-09) fixed the vocabulary and the flow but looked like what it was: a
generated dashboard. A sans-serif interface face, a violet accent, tinted icon tiles, rounded
cards with soft shadows in a grid, a hero with three cards beneath it. The owner's reaction was
that it did not feel like a creative tool.

## What the research said

Sources are listed at the end. They are practitioner writing and design-system write-ups rather
than formal research, and several overlap, so they were used for direction, not as rules.

- **Why generated interfaces look alike.** Models reach for the statistical middle: Inter or a
  system sans, purple-to-blue gradients, cyan on dark, identical card grids, glass and glow, and
  the "hero, three cards, footer" page. The recurring advice is to decide a specific direction and
  one memorable element before building, commit to a restrained palette with a single sharp
  accent, tint neutrals rather than using pure black and white, and prefer one orchestrated
  motion to many small ones.
- **What editorial and literary sites do instead.** Serif text and oversized optical display type;
  warm paper and near-black ink; one accent, used rarely; hairline rules rather than shadows;
  square surfaces; narrow reading columns; asymmetry and white space. The New Yorker's site was
  the most-cited reference for restraint.
- **What recognised WebGL work has in common.** One strong visual idea carried through; custom
  shaders; scroll as the storyteller; and strict budgets: capped pixel ratio, render only while
  visible, respect reduced motion, and a page that still works with the effect removed. WebGL2 is
  the safe baseline in 2026; WebGPU has shipped in the major browsers but is still treated as an
  enhancement.
- **Writing tools.** The ones writers praise are quiet: the interface recedes and the text is the
  subject. Planning features sit beside the writing, not in front of it.
- **Assistant onboarding.** A blank "how can I help?" box raises the effort of starting.
  Structured starting points, examples in place, and a narrow first success work better;
  conversation and a workspace side by side beat chat alone.

## The direction: ink on paper

Novel Engine is a book about your book. It should look like printed matter with one living thing
in it.

- **One idea.** Ink finding its threads: a story's facts connecting into something that holds.
- **Type carries the design.** Newsreader, a text serif with optical sizes, sets everything that
  belongs to the story: names, facts, descriptions, headings. Instrument Sans sets the controls
  and labels. Both are bundled; nothing loads from another origin.
- **Colour.** Warm paper, near-black ink, and one accent, cinnabar (the colour of a seal or an
  editor's pencil). The accent marks: the current page, an underline, a numeral, the brand seal.
  It never fills a button. Entry types get muted pigments (indigo, verdigris, ochre, madder,
  moss, umber) used as small seals, never as panels.
- **Rules, not boxes.** Sections are separated by hairlines and space. No drop shadows except on
  a dialog, which is a sheet lifted off the page. Corners are nearly square.
- **Layouts borrowed from print.** The story bible is an index in columns. An entry is a
  dictionary entry: headword, part of speech, sentences. The project home is a title page with
  figures set in a ruled row. Projects are books on a shelf. Navigation is a table of contents.
- **Paper.** A faint grain so flat colour reads as a surface.
- **Dark theme.** The same desk at night: warm black, cream ink, the accent lightened.
- **Motion.** One arrival sequence on the landing page and the ink itself. Dialogs rise briefly.
  Nothing else moves. Reduced-motion is respected everywhere.

## The landing page

- **Hero.** A full-height WebGL2 fragment shader of ink marbling on paper: fractal noise warped
  through itself twice and read as bands, with a vein of cinnabar. It drifts slowly, yields
  slightly to the pointer, and shifts with scroll. It keeps the side the words sit on quiet (right
  on wide screens, below on tall ones). Colours are read from the theme, so it follows light and
  dark.
- **Budget and fallbacks.** Pixel ratio capped at 1.5; it renders only while on screen and the tab
  is in front; with reduced motion it draws one still frame; without WebGL2 a CSS wash shows
  instead. The page is complete without it.
- **Content.** Set as editorial pages: a statement and a drop-capped paragraph, an errata slip,
  three numbered chapters with printed "specimens" of the product, a dictionary-style entry, and a
  ladder of the five levels that says which exist today and which are being built.
- **Honesty.** The page describes what the product does today and labels what is coming. It does
  not claim to catch contradictions in prose, because that is not built.

## What was deliberately not done

- **WebGPU.** A single full-screen fragment shader gains nothing from it, and WebGL2 runs
  everywhere. It remains available for later, heavier work (a navigable story map, for instance)
  as an enhancement with a WebGL2 fallback.
- **3D scenes and scroll-jacking.** Impressive, but they would put spectacle in front of the text
  and cost load time on a page whose job is to explain.
- **A component framework.** The look depends on typography and spacing that frameworks fight.
- **WebGL inside the app.** The working interface stays still and quiet.

## Still to do

- A real first-run and the assistant as a persistent surface (Phase 2.5); today the assistant
  opens as a dialog.
- A story map: a navigable picture of entries and the facts between them, where WebGL or WebGPU
  would earn its place.
- Review on a phone-sized screen has been checked only for layout, not used.
- The owner has not yet seen this direction in use; expect a round of adjustment.

## Sources

- [Why your AI keeps building the same purple gradient website](https://scour.ing/@emschwartz/p/https://prg.sh/ramblings/Why-Your-AI-Keeps-Building-the-Same-Purple-Gradient-Website)
- [Impeccable: anti-patterns](https://www.mintlify.com/pbakaus/impeccable/concepts/anti-patterns)
- [Anti AI-slop ban list](https://github.com/Laith0003/ux-skill/wiki/Anti-AI-slop-ban-list.md)
- [The anti AI-slop UI design skill](https://medium.com/@porter.nicholas/anthropic-skills-marketplace-the-anti-ai-slop-ui-design-skill-a572d0cfef4f)
- [The New Yorker design analysis](https://www.shadcn.io/design/newyorker/raw)
- [Paper Website design notes](https://www.webdesignhot.com/design.md/paper-website/)
- [Atelier: editorial serif system](https://app.superdesign.dev/library/atelier-design-spoken-into-being-editorial-serif-burgundy)
- [Sunday Editorial design system](https://www.visily.ai/design-systems/sunday-editorial)
- [Best award-winning websites 2026](https://www.hontran.dev/blog/best-award-winning-websites-2026)
- [WebGL website examples](https://www.hontran.dev/blog/webgl-website-examples)
- [Awwwards 2026 and 3D](https://svilenkovic.com/3d/awwwards-2026-3d)
- [WebGL and WebGPU at SIGGRAPH 2026 (Khronos)](https://www.khronos.org/developers/linkto/webgl-and-webgpu-sigg26)
- [WebGPU vs WebGL in 2026](https://app.cinevva.com/guides/webgpu-vs-webgl-games)
- [Domain-warped noise](https://gamedev.net/shaderlab/23-domain-warped-noise/)
- [Reedsy: writing tools](https://reedsy.com/blog/writing-tools/)
- [The best writing apps for Mac in 2026](https://timingapp.com/blog/best-writing-apps/)
- [Onboarding AI UX patterns](https://www.aiuxplayground.com/patterns/onboarding)
- [LLM chat UX patterns](https://www.parallelhq.com/blog/llm-chat-ux-patterns)
- [Collaborating with GenAI canvas interfaces](https://reedhepler.substack.com/p/collaborating-with-genai-canvas-interfaces)
