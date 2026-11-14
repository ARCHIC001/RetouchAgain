"""One explicit G1 coarse-WB exception; pure guards, no native or parameter choice."""
import copy

from photo_stage_rejections import require_action


def validate_request(state, review, observation):
    workflow = state['workflow']
    require_action(workflow['phase'] == 'G1' and workflow['accepted_versions']['tone'] is None,
                   'pre_tone_wb_phase', 'Coarse WB is only available in the initial G1 before Tone.', 'action.name')
    require_action(workflow.get('pre_tone_wb') is None, 'pre_tone_wb_once',
                   'The single coarse-WB opportunity is already used or has an unknown outcome; never replay.', 'action.name')
    require_action(observation.get('comparison_mode', 'target') == 'target'
                   and observation.get('stage_observation', {}).get('view_mode') == 'color',
                   'pre_tone_wb_target', 'Coarse WB needs the actual G1 color author/current views.', 'review')
    impact = review.get('tone_basis_impact')
    require_action(isinstance(impact, str) and bool(impact.strip()), 'pre_tone_wb_impact',
                   'Explain how a severe cast obstructs the tone basis, while preserving intended environment color.',
                   'review.tone_basis_impact')
    assessment = review.get('wb_assessment')
    require_action(isinstance(assessment, dict) and assessment.get('wb_task_status') == 'needed',
                   'pre_tone_wb_assessment', 'Explicit current WB assessment must justify needed coarse WB; no neutral reference is valid.',
                   'review.wb_assessment')


def accepted_effect(state, review, observation):
    """Bind only an accepted actual effect review; a failed action never calls this."""
    record = state['workflow'].get('pre_tone_wb')
    effect = review.get('effect_review')
    if not record or record.get('status') != 'succeeded' or not effect:
        return
    actual = state['versions'].get(record['child_version'], {})
    if (observation['version'] == record['child_version'] and observation.get('comparison_mode') == 'effect'
            and effect.get('expectation_id') == actual.get('expectation_id')
            and any(row.get('expectation_id') == effect['expectation_id'] and row.get('observation_id') == observation['id']
                    for row in state.get('effect_reviews', []))):
        record.update(effect_review_observation_id=observation['id'],
                      effect_review_observation_index=next(i for i, row in enumerate(state['observations'])
                                                         if row['id'] == observation['id']),
                      effect_review=copy.deepcopy(effect))


def validate_exit(state, observation, full_observation):
    record = state['workflow'].get('pre_tone_wb')
    if not record:
        return
    require_action(record.get('status') == 'succeeded', 'pre_tone_wb_unknown',
                   'Coarse-WB outcome is not settled; retain evidence and do not enter Tone.', 'action.name')
    require_action(bool(record.get('effect_review_observation_id')), 'pre_tone_wb_effect_review',
                   'Review the actual coarse-WB parent/child effect before selecting the tone basis.', 'review.effect_review')
    full_index = next(i for i, row in enumerate(state['observations']) if row['id'] == full_observation['id'])
    require_action(full_index > record['effect_review_observation_index'], 'pre_tone_wb_fresh_target',
                   'After the accepted effect review, inspect a fresh full G1 author/current target view before Tone.', 'action.name')
    require_action(full_observation['version'] == observation['version'], 'pre_tone_wb_selected_target',
                   'The selected tone basis must be the version in that fresh target view.', 'action.arguments.selected_version')


def validate_transfer(source, continued):
    """A new scope cannot erase a used or in-flight task-level coarse-WB record."""
    old = source.get('workflow', {}).get('pre_tone_wb')
    new = continued.get('workflow', {}).get('pre_tone_wb')
    if old != new:
        raise ValueError('Continuation must preserve the exact coarse-WB lifecycle record')
    if old and old.get('status') != 'succeeded':
        raise ValueError('Coarse-WB in-flight/unknown outcome cannot continue or replay')
