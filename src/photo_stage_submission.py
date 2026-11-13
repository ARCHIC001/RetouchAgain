"""Pure, lossless stage-envelope normalization; no runtime/native imports.

Only explicit public metadata is relocated. Native operations and values are
never generated, trimmed, coerced or corrected. Closed schemas still validate.
"""
import copy
import json
import sys

REVIEW_FIELDS = ('group_reviews', 'effect_expectation', 'module_decisions', 'judgment_items')
NARRATIVE = {'note', 'uncertainty', 'uncertainty_note', 'outcome_alt', 'expected_change_note'}
PROPOSAL_CHANGES = {'branch', 'diagnosis', 'priority', 'proposed_controls', 'evidence', 'scope',
                    'target_change', 'protected', 'risks', 'dependencies', 'groups', 'module',
                    'step', 'reorder_override', 'selection_reason'}


def normalize_submission(submission):
    """Return (canonical, warnings), detached from input and idempotent.

    Conflicting redundant fields stay in metadata_extra, never merged. Missing
    substantive review/action fields stay missing for ordinary schema feedback.
    """
    canonical = copy.deepcopy(submission)
    warnings = []
    if not isinstance(canonical, dict):
        return canonical, warnings

    def retain(owner, key, path, code):
        value = owner.pop(key)
        entry = {'path': path, 'value': copy.deepcopy(value), 'reason': code}
        warnings.append({'code': code, 'field_path': path, 'metadata_extra': entry})

    action = canonical.get('action')
    arguments = action.get('arguments') if isinstance(action, dict) else None
    if isinstance(arguments, dict):
        if action.get('name') == 'photo_observe' and arguments.get('comparison_mode') == 'effect':
            reference = arguments.get('reference_roi')
            candidate = arguments.get('candidate_roi')
            if reference is not None and (candidate is None or reference == candidate):
                if candidate is None:
                    arguments['candidate_roi'] = copy.deepcopy(reference)
                retain(arguments, 'reference_roi', 'action.arguments.reference_roi', 'effect_shared_roi')
        for key in ('selection_reason', 'override_reason'):
            if key not in arguments:
                continue
            if key not in canonical:
                canonical[key] = arguments.pop(key)
                warnings.append({'code': 'selection_reason_relocated', 'field_path': 'action.arguments.' + key,
                                 'canonical_path': key})
            else:
                retain(arguments, key, 'action.arguments.' + key, 'selection_reason_conflict')

    review = canonical.get('review')
    for key in REVIEW_FIELDS:
        if key not in canonical:
            continue
        if review is None and 'review' not in canonical:
            review = canonical['review'] = {}
        if not isinstance(review, dict):
            continue
        if key not in review:
            review[key] = canonical.pop(key)
            warnings.append({'code': 'review_field_relocated', 'field_path': key,
                             'canonical_path': 'review.' + key})
        else:
            retain(canonical, key, key, 'review_field_conflict' if canonical[key] != review[key]
                   else 'redundant_review_field')

    if isinstance(review, dict):
        if 'review_debt_updates' in review:
            if 'review_debt_updates' not in canonical:
                canonical['review_debt_updates'] = review.pop('review_debt_updates')
                warnings.append({'code': 'review_debts_relocated', 'field_path': 'review.review_debt_updates',
                                 'canonical_path': 'review_debt_updates'})
            else:
                retain(review, 'review_debt_updates', 'review.review_debt_updates', 'redundant_review_debts')
        allowed = {
            'review': {'visible_comparison', 'previous_outcome', 'remaining_gaps', 'uncertainty',
                       'judgment_items', 'module_decisions', 'effect_expectation', 'effect_review', 'group_reviews'},
            'module_decisions': {'module', 'choice', 'reason'},
            'effect_review': {'expectation_id', 'outcome', 'evidence'},
            'effect_expectation': {'target', 'expected_change', 'protected_region', 'risk', 'falsifier'},
        }
        for key in list(review):
            if key not in allowed['review']:
                retain(review, key, 'review.' + key, 'public_review_metadata')
        for key in ('effect_review', 'effect_expectation'):
            row = review.get(key)
            if isinstance(row, dict):
                for extra in list(row):
                    if extra not in allowed[key]:
                        retain(row, extra, 'review.' + key + '.' + extra, 'public_review_metadata')
        for i, row in enumerate(review.get('module_decisions', [])) if isinstance(review.get('module_decisions'), list) else []:
            if isinstance(row, dict):
                for extra in list(row):
                    if extra not in allowed['module_decisions']:
                        retain(row, extra, 'review.module_decisions[' + str(i) + '].' + extra, 'public_review_metadata')
        targets = [(review, 'review', NARRATIVE - {'uncertainty'})]
        for key in ('module_decisions', 'group_reviews'):
            rows = review.get(key)
            if isinstance(rows, list):
                targets.extend((row, 'review.' + key + '[' + str(i) + ']', NARRATIVE)
                               for i, row in enumerate(rows) if isinstance(row, dict))
        for key in ('effect_review', 'effect_expectation'):
            if isinstance(review.get(key), dict):
                targets.append((review[key], 'review.' + key, NARRATIVE))
        for owner, path, extras in targets:
            for key in sorted(extras & owner.keys()):
                retain(owner, key, path + '.' + key, 'narrative_metadata_relocated')
        for i, row in enumerate(review.get('group_reviews', [])) if isinstance(review.get('group_reviews'), list) else []:
            if not isinstance(row, dict):
                continue
            path = 'review.group_reviews[' + str(i) + ']'
            if row.get('operation_id') == '':
                retain(row, 'operation_id', path + '.operation_id', 'empty_optional_operation_reference')
            if row.get('outcome') == 'keep' and PROPOSAL_CHANGES & row.keys():
                row['outcome'] = 'revise'
                warnings.append({'code': 'keep_with_changes_revised', 'field_path': path + '.outcome',
                                 'original': 'keep', 'canonical': 'revise'})

    for i, row in enumerate(canonical.get('bank_delta', [])) if isinstance(canonical.get('bank_delta'), list) else []:
        if not isinstance(row, dict):
            continue
        path = 'bank_delta[' + str(i) + ']'
        for key in ('base_version', 'observation_id'):
            if key in row:
                retain(row, key, path + '.' + key, 'proposal_source_metadata_relocated')
        if row.get('op') == 'keep' and PROPOSAL_CHANGES & row.keys():
            row['op'] = 'revise'
            warnings.append({'code': 'keep_with_changes_revised', 'field_path': path + '.op',
                             'original': 'keep', 'canonical': 'revise'})
    if 'review_debt_updates_note' in canonical:
        retain(canonical, 'review_debt_updates_note', 'review_debt_updates_note', 'narrative_metadata_relocated')
    return canonical, warnings


if __name__ == '__main__':
    canonical, warnings = normalize_submission(json.load(sys.stdin))
    json.dump({'canonical': canonical, 'warnings': warnings}, sys.stdout, ensure_ascii=False, allow_nan=False)
    sys.stdout.write('\n')
