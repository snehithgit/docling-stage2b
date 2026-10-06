# V5.0.6: Intent-aware scoped evidence recovery

Question planning distinguishes troubleshooting, alarms, procedures, part identifiers, specification values, safety constraints, references and definitions. Simple problem statements such as “motor not running” also route to troubleshooting. Candidate role coverage reports causes/actions/values separately and checks requested literal measurements without unit conversion: a thermostat threshold or cooling procedure does not establish an overheating cause. Coverage is heuristic, not answer verification.

When candidate roles are missing, retrieval runs at most two lexical expansions against the already selected book/equipment index paths. The complete original question remains an unchanged prefix, preserving identifiers, units and negation. There is no global/manual fallback, provider change, Colab inference or additional embedding-model query. If the selected hybrid service/index fails, recovery does not hide that error with lexical fallback.

Up to six topical, role-matching candidates are retained. Protected identifiers must match literally. Existing primary results retain their order; recovery is attached as additional candidates and enters the shared question packet budget. Empty primary results can use matching recovered rows. Recovery candidates are rebound to current source ledgers and equipment/manual scope before answer selection. Rejected, stale, unparsed and otherwise ineligible evidence is withheld.

The UI shows recovered passages and remaining candidate-role gaps. Grounded-source listings identify recovered evidence, and prompt exports/generation include advisory coverage information. Visible objects and model summaries do not count as literal causal coverage. Candidate-role matches favor direct evidence in packet selection without claiming semantic certainty.

This release recovers material already present in indexes. It cannot invent unreadable content or repair missing diagram extraction. Pending image/table validation still applies. Keyword coverage can miss paraphrases or include an imperfect match; semantic claim and relationship verification is Phase 8.
