# Novel Engine Architecture

> Historical concept document. The maintained, implementation-oriented architecture and roadmap now start at [`plan/README.md`](plan/README.md). Preserve this file as the original design record; update `plan/` for new decisions.

## 1. Core Concept
A RAG-based novel engine that maintains a consistent, evolving world state using a Knowledge Graph (KG). It differentiates between objective reality ("God View") and subjective character perspectives ("Beliefs").

## 2. Knowledge Graph Schema

- **Entity**: `Character`, `Location`, `Object`, `Faction`, `Concept`, `Resource`, `Appearance`, `PersonalityProfile`, `State`, `Belief`, `Goal`
- **World**: `System` (Magic, Political, Tech), `Pressure` (Conflict, Scarcity), `Law`, `Constraint`
- **Event**: `Scene`, `Action`, `Flashback`, `Interaction` (Properties: `category`, `chapter`, `timeline_index`, `summary`, `flags`)
- **Meta**: `StoryBranch`, `WorldState`, `Source` (e.g., "User Input", "Inference")

### 2.2. Edge Types & Properties
All edges should support **Temporal Properties** (`valid_from`, `valid_until`) and **Provenance** (`source_event_id`).

*   **Structural**: `LOCATED_AT`, `PART_OF`, `OWNS`, `MEMBER_OF`, `OCCURRED_AT`, `CONTAINS`, `HAS_APPEARANCE`, `HAS_PERSONALITY`, `CURRENT_STATE`, `BELIEVES`, `HAS_GOAL`
*   **Systemic**: `HAS_SYSTEM`, `HAS_PRESSURE`, `REQUIRES_RESOURCE`, `FORBIDDEN_BY`, `AFFECTS`, `CAUSES`, `DEPENDS_ON`
*   **Social**: `KNOWS`, `LIKES`, `HATES`, `RELATED_TO` (properties: `intensity`, `secret_level`), `PARTICIPATED_IN`, `TRAUMATIZED_BY`
*   **Epistemic (Beliefs)**:
    *   **Direct**: `(Character)-[:BELIEVES]->(Statement)`
### 2.5. Character Architecture (Modular)
Characters use a stable anchor node with dynamic sub-nodes:
- **Character (Anchor)**: Identity-defining properties (id, name, role, archetype, birthdate).
- **Appearance**: (Character)-[:HAS_APPEARANCE]->(Appearance) {height, build, hair, marks}
- **Personality**: (Character)-[:HAS_PERSONALITY]->(PersonalityProfile) {traits, style, alignment}
- **State**: (Character)-[:CURRENT_STATE]->(State) {emotions, trust_levels, chapter}
- **Beliefs**: (Character)-[:BELIEVES]->(Belief) {about, claim, confidence, is_true}

### 2.3. Temporal & Branching Model
To handle time and "Git-like" branching:
1.  **Global Timeline**: A main backbone of `Event` nodes linked by `NEXT` edges.
2.  **Valid Time**: Edges have `[start_tick, end_tick]`.
3.  **Branching**: `WorldState` nodes represent the state at a specific point in a specific branch. Mutations create new `WorldState` nodes (Copy-on-Write for changed facts).
    *   *Optimization*: Store deltas (additions/deletions) per Scene.

### 2.4. Narrative (Story) Graph
A separate but connected subgraph representing **Narrative Intent**.
- **Nodes**: `Story`, `Act`, `PlotArc`, `Beat`, `Scene_Plan`, `Foreshadow`, `Payoff`, `Theme`, `Goal`, `Conflict`.
- **Intra-Story Edges**: `HAS_ACT`, `HAS_ARC`, `HAS_THEME`, `STARTS_AT`, `CLIMAX_AT`, `INVOLVES`, `OCCURS_IN`, `AFFECTS`.
- **Inter-Graph Edges (Story -> World)**:
    - `REQUIRES_STATE`: Links a `Beat` to a specific `Fact` or `Belief` in the World Graph.
    - `TRIGGERS_EVENT`: Links a `Beat` to a `Scene` (Reality).
    - `CHANGES_STATE`: Intended delta to be applied to the World Graph upon resolution.
    - `RESOLVES`: Links a `Beat` to a `SecretNode` or `Conflict`.

## 3. System Architecture

### 3.1. Components
1.  **Ingestion/World Building**:
    *   **Questionnaire Engine**: LLM (Grok/OpenAI) generates questions (World/Character).
    *   **Design Extractor**: Vision Model (e.g., GPT-4o) analyzes uploaded images -> extracted attributes -> KG Nodes.
    *   **Triple Extractor**: LLM parses text -> semantic triples -> KG.

    **Fast Knowledge Update Interface (The "God Hand")**:
    *   **Natural Language Parser**: A dedicated LLM agent that interprets natural language updates.
    *   **Quick-Link UI**: A streamlined interface to manually inject/edit triples.
    *   **Incremental Patching**: API to accept partial updates.

2.  **Frontend (React)**:
    *   **World Visualizer**: A graph-based interface to explore the Knowledge Graph (e.g., using `react-force-graph` or `vis.js`).
    *   **Story Interface**: A dashboard for storyline planning (Story Graph) and scene generation.
    *   **Knowledge Update Tool**: The manual/natural language command center for the "God Hand".

2.  **State Manager (The "Game Engine")**:
    *   Maintains the `CurrentState` pointer.
    *   Handles "Tick" advancement (Time).
    *   **Belief Propagation**: If `CharA` sees `CharB` do `Action`, update `CharA`'s beliefs.

3.  **Retrieval Engine (RAG 2.0)**:
    *   **Dual-Database**: Neo4j (Knowledge Graph) is the deterministic source of truth. ChromaDB (Vector DB) stores semantic content, stylistic recall, and scene summaries.
    *   **Context Budgeting**: Top-k retrieval is balanced with token budgets (e.g., Canon: 100t, Narrative: 200t, World: 200t) to avoid degrading quality.
    *   **Validation**: A fast LLM validates retrieved documents for true relevance before generation.

4.  **Generation Engine**:
    *   **Storyteller**: LLM generates scene text based on query + retrieved context.
    *   **Simulator**: separate LLM pass to update the KG based on the generated text (or generate the text *based* on a simulation).

5.  **Consistency "Critic"**:
    *   Post-generation check.
    *   Validates: Physical possibility (timeline), Character Voice, Knowledge constraints (did Character X know Secret Y?).
    *   **Narrative Alignment**: Validates if the generated scene fulfills the `Beat` requirements from the Story Graph (e.g., "Did the trust actually increase?").

### 3.3. Narrative Planner
- **Intent Engine**: LLM acts as an "Author," planning the sequence of Beats and Arcs *before* the Storyteller generates narrative text.
- **Bridge Logic**: Monitors World Graph state vs. Story Graph requirements. If `REQUIRES_STATE` is not met, the Narrative Planner must generate bridge scenes or adjust the plan.

### 3.2. Integration APIs
*   **OpenAI (GPT-4o)**: Complex reasoning, Image analysis, Critic.
*   **Grok (Grok-1)**: Long-context story generation, massive retrieval windows.

## 4. Data Flow (Scene Generation)
1.  **User Input/Prompt**: "Alice confronts Bob about the stolen ring."
2.  **Retrieval**:
    *   Fetch Alice & Bob profiles (at current time).
    *   Fetch "Stolen Ring" facts.
    *   Fetch Alice's *belief* about where the ring is.
    *   Fetch Bob's *secret* (he has it).
3.  **Context Construction**: Assemble "Fact Sheet" + "Belief Sheet".
4.  **Drafting**: LLM generates narrative.
5.  **Extraction & Update**:
    *   Extract new facts (Alice shouts at Bob).
    *   Update Time.
    *   Store new "Scene" node.
6.  **Consistency Check**: Verify against constraints.

## 5. Security/Secrets
*   **Secret Node**: `Secret(ID: 123, Content: "Bob stole the ring")`.
*   **Access**: `(Bob)-[:KNOWS]->(Secret 123)`.
*   **Leakage Risk**: If `(Alice)-[:HEARS]->(Event: Bob talks to Self)`, probabilistically check whether the secret edge should be created for Alice.

## 6. Dual-Database RAG Architecture
The engine uses a "Two-Brain" approach. The Neo4j Knowledge Graph (KG) serves as the **deterministic source of truth**, while ChromaDB (Vector DB) stores semantic content, stylistic recall, and narrative summaries. The Vector DB **never overrides** the KG.

### 6.1. Vector DB Scope & Schema
The Vector DB stores immutable, append-only chunks. Old versions are marked with `active=false`. It determines if the "God Hand" should target the KG (hard facts) or Vector DB (stylistic/semantic facts).
**Vector Schema**:
*   `ID`: Shared stable ID with the KG where applicable (e.g., `event_brother_execution`).
*   **Type**: `scene summary`, `character summary`, `rule summary`, `world summary`, `timeline summary`, `faction summary`, `item summary`, `planning document`.
*   `entity_refs`: List of KG Node IDs involved.
*   `timeline_position`: Chronological anchor.
*   `world_arc`: The overarching storyline/branch.
*   `active`: Boolean (true for latest version).
*   `version`: Integer.

### 6.2. 2-Index Strategy
*   **Canon Index**: Character canonical bios, personality/speech samples, current state snapshots, rule descriptions/examples/exceptions, timeline summaries, world lore.
*   **Narrative Index**: Scene summaries, Event blocks (POV/Location shifts), Full scenes (for style), Dialogue samples.

### 6.3. Scene Chunking (3 Layers)
1.  **Layer 1 (Scene Summary)**: 150-300 tokens. Captures who was present, what happened, rules involved, emotional turning points, state changes.
2.  **Layer 2 (Event Blocks)**: 500-1000 tokens. Semantically chunked by POV shifts, location changes, major action beats.
3.  **Layer 3 (Full Scene)**: The exact text for stylistic recall.

### 6.4. Querying Engine
**3-Level Strategy:**
1.  **Level 1 (Deterministic Queries)**: Query KG first to identify active characters, current locations, and constraints. Use these IDs to filter the Vector DB.
2.  **Level 2 (LLM-Assisted Expansion)**: Translate general queries into 3-5 specific semantic queries (e.g., "Moments where X doubted Y's loyalty"). Fetch 2-3 chunks per query, then merge and deduplicate.
3.  **Level 3 (Reflective Querying)**: Post-drafting, an LLM queries the Vector DB for unresolved threads or past events to ensure continuity.

**Guardrails:**
*   **KG Triggered Retrieval**: Search KG for "magic bullet" -> get event IDs -> filter Vector DB by those IDs -> retrieve narrative blocks.
*   **Knowledge Gating**: Ensure retrieved facts are strictly known by the POV character before inclusion in the Storyteller prompt.

### 6.5. State Synchronization & Scene Intention
*   **Scene Intention**: Pre-generation planning requires explicit definition of scene goal, conflict axis, character shift target, rule pressure, and thread progression.
*   **Contradiction Check**: Upon scene generation, the Consistency Critic validates the scene against the KG.
*   **State Updates**: State changes (e.g., trust shifts) are mutated in the KG. The system then generates a new "Current State Snapshot" paragraph and appends it to the Vector DB as the active version.
*   **Periodic Memory Compression**: Older scenes undergo LLM compression to replace verbose L2 blocks with concise L1 summaries in the Vector DB.

### 6.6 Narrative Thread Tracking Options
Tracking unresolved threads (e.g., mysteries, promises) requires a dedicated mechanism. Options:
*   **Option A: KG-Native (Current)**. Use `Conflict` or `PlotArc` nodes connected via `RESOLVES` edges. *Pros*: Tightly coupled to deterministic truth. *Cons*: Hard to capture nuanced, semantic tension.
*   **Option B: Vector DB Planning Docs**. Store "unresolved thread" summaries in ChromaDB. *Pros*: Excellent semantic retrieval during Level 3 Querying. *Cons*: Might drift from actual KG facts if not synced.
*   **Option C: Hybrid (Recommended)**. `Thread` nodes in KG track binary open/closed status, linking to Vector DB "Planning Documents" for the semantic context.

## 7. Frontend Tagging & Entity Resolution
To bridge frontend user input with deterministic backend Knowledge/Vector graphs, the system uses a strictly formatted Markdown Link approach for `@` mentions.

### 7.1. Tag Format (The Entity URN)
When a user types `@` and selects an entity in the frontend editor, the editor must insert the following raw markdown string into the text:
`[Display Name](entity:<node_type>:<node_id>)`

**Example:**
*Raw Text:* `He struck [Arin](entity:Character:char_arin) with the [Sun Blade](entity:Object:item_sun_blade).`
*Rendered:* `He struck @Arin with the @Sun Blade.`

### 7.2. The Resolution Pipeline (RAG Setup)
1. **Extraction**: Before sending a prompt/plan to the generation engine, the backend strips the text using Regex `\[(.*?)\]\(entity:(.*?):(.*?)\)` to isolate all explicit `node_id`s.
2. **Deterministic Context**: The backend uses these IDs to query the Neo4j KG (e.g., `MATCH (n) WHERE n.id IN $extracted_ids`), pulling exact Character profiles, states, and beliefs into the context window.
3. **Vector Filtering**: The extracted IDs are passed to ChromaDB as `entity_refs` filters (`{"entity_refs": {"$in": extracted_ids}}`) to fetch relevant historical scenes without risking generic name collisions.
4. **LLM Enforcement**: The Drafting LLM is explicitly instructed to maintain and output this same `[Name](entity:Type:ID)` format for all entities it generates or references, guaranteeing the backend can parse the newly generated scene back into the KG seamlessly.
