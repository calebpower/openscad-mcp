# Mutation records for the `witness-marks` branch

**NOTHING IN THIS DIRECTORY GOES UPSTREAM.**

These are Witness Marks' own methodology, driven by `tools/mutate.sh` in the
witness-marks repository, which lives beside this checkout. They exist so that
work done here carries the same burden of proof as work done there: every new
assertion is mutation-checked and the kill is recorded.

A mutation harness in this repository would be the *methodology transplant*
that Witness Marks' own spec decided not to push upstream -- dozens of changes
across `tests/`, CI and structure, against a maintainer whose CONTRIBUTING
rightly refuses large diffs inside functional changes. So these records stay on
the `witness-marks` branch, and a feature branch cherry-picked for an upstream
pull request must carry **its tests in this project's idiom and nothing of
ours**.

A mutant is a directory of literal files: `subject`, `find`, `replace`,
`expect-fail`, `test-cmd`. The driver refuses rather than guessing when the
find text is not unique, when the baseline is already red, when the subject is
not restored, or when a test other than the named one dies.

`--color=no` is not optional in `test-cmd`. The driver matches pytest's
`FAILED <path>::<name>` at the start of a line, and ANSI escapes break the
anchor -- a real kill then reports as a MISFIRE.

## coincident -- 8 mutants, 8 kills

| mutant | the lie it tells | killed by |
| --- | --- | --- |
| `coincident-nominal-radius` | the faceted radius gives the worst-case wall | `test_the_worst_case_wall_is_the_nominal_radius_not_the_faceted_one` |
| `coincident-bracket-collapsed` | both ends of the bracket are the same figure | same |
| `coincident-additive-included` | additive features share walls too | `test_additive_features_are_not_compared` |
| `coincident-skew-measured` | one wall figure describes two skew cylinders | `test_non_parallel_pairs_are_counted_and_not_measured` |
| `coincident-tolerance-ignored` | every pair is worth reporting | `test_a_healthy_wall_reports_nothing` |
| `coincident-merged-excused` | a facet error can excuse a negative wall | `test_a_merged_pair_is_not_excused_by_the_tessellation_bound` |
| `coincident-no-provider-passes` | no CSG dump means nothing is wrong | `test_the_rule_is_unresolved_without_a_provider` |
| `coincident-rule-tolerance-hardcoded` | the tool's tolerance is the rule's | `test_the_rule_tolerance_comes_from_the_rule` |

The first two matter most. `cylinder($fn=n)` puts its polygon vertices ON the
nominal circle, so a bore reaches full nominal radius at every vertex and the
wall is thinnest where two vertices face each other. The faceted radius gives
the THICKEST wall. The first version of `coincident_features` had that
backwards and would have claimed more material than the part has anywhere;
these two mutants are that error, pinned so it cannot return.
