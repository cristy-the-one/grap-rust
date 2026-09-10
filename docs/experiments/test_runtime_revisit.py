import copy
import pytest
from evaluate_selective_thinking import validate_escalation


@pytest.mark.parametrize('field', ['evidence_sha256', 'prompt_sha256', 'code_fingerprint'])
def test_escalation_rejects_mixed_experiment_generations(field):
    current = {'evidence_sha256': 'responses', 'prompt_sha256': 'prompts',
               'provenance': {'code_fingerprint': 'current-code'}}
    previous = copy.deepcopy(current)
    validate_escalation(previous, current)
    target = previous['provenance'] if field == 'code_fingerprint' else previous
    target[field] = 'older'
    with pytest.raises(ValueError):
        validate_escalation(previous, current)
