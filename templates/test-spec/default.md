---
id: default
name: Default test specification
level: software
---
# Test specification: {{title}}

{{#cases}}
## {{id}} ({{kind}})
Verifies: {{verifies}}
Preconditions: {{preconditions}}
Stimulus: {{stimulus}}
Expected result: {{expected_observable_result}}
Pass criterion: {{pass_criterion}}
{{/cases}}
