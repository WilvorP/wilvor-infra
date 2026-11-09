# Sprint 3B.2 gateway deployment review

Runtime admission status: **NOT_ATTESTED**. Production principal issuance is unavailable. `ConfigurationRestrictionDecision.RESTRICTED` describes a server-owned manifest that meets the syntactic route contract. It is not a grant, not a live AWS attestation, and not permission to enable `POST /ai/query`.

The current Terraform default is `enable_ai_query_route = false`. This review does not change Terraform, does not call AWS, and does not enable the route.

## Blockers

These remain closed after a restricted configuration check:

- Live API id, account, region, stage deployment, resource policy, identity-based invoke, alias target, function URL, and log group have not been inspected. Every report leaves those facts as `NOT_YET_INSPECTED`, and every issuance control stays `satisfied = false`.
- A same-account IAM principal with `lambda:InvokeFunction` can call the function even when the API Gateway resource-policy statement is narrow. That path is an alternate invoke source until IAM is reviewed.
- `modules/operational_api/api.tf` does not set `authorization_scopes` on `POST /ai/query`. OAuth scopes are not enforced at the edge. Scope strings inside an event are not a resource policy.
- Inspecting a Lambda event cannot make API Gateway provenance cryptographic. A direct invoke can carry a forged HTTP API v2 document.
- `wilvor_ai.master_gateway_identity.PRODUCTION_GATEWAY_ISSUANCE_AVAILABLE` stays false. This module has no setter and is not the production principal issuer.
- Wave 1 does not deploy an AI Lambda, attach a reviewed resource policy, or turn the route on.

## Why a direct invoke can forge the event

A directly invoked Lambda receives the invoker's JSON as the event argument. API Gateway validates a JWT only at the edge, then calls lambda:InvokeFunction as apigateway.amazonaws.com. That invoke carries no signature over the event document. Any principal the function's resource policy or a same-account identity policy allows to call lambda:InvokeFunction can submit an HTTP API payload format 2.0 document with a chosen routeKey, stage, request id, and requestContext.authorizer.jwt object. The function cannot tell that document from a gateway invoke by reading the event, the Lambda context, or a caller-supplied trusted flag. Resource-policy source ARN, identity-policy invoke rights, and the absence of other invoke paths have to be reviewed in AWS before production issuance can be considered. Inspecting the event cannot make that provenance cryptographic.

`requestContext`, a request id, a JWT-looking `authorizer.jwt` object, the `Authorization` header, Lambda context (`aws_request_id`, `invoked_function_arn`), and `trusted=True` are untrusted inputs. The checker records them and does not copy them into verified configuration. It does not decode the `Authorization` header and it does not verify a signature.

## What the source configures

Read from `modules/operational_api/api.tf` and `variables.tf`. These are repository facts, not a description of a deployed account.

- `variable "enable_ai_query_route"` is a non-null bool whose default is `false`. The AI authorizer, integration, route, and Lambda permission resources use `count` and stay absent unless the flag is true and the external issuer, audience, and Lambda values pass the module preconditions.
- The AI route key is `POST /ai/query`. The route sets `authorization_type = "JWT"` and `authorizer_id` to `aws_apigatewayv2_authorizer.ai_jwt`. There is no `NONE` authorization and no `authorization_scopes` argument.
- The JWT authorizer uses `authorizer_type = "JWT"`, identity source `$request.header.Authorization`, `issuer = var.ai_jwt_issuer`, and `audience = var.ai_jwt_audience`. This module does not create Cognito, an OIDC provider, or the audience.
- The AI integration is `AWS_PROXY` / `POST` / payload format `2.0`. Its URI is `var.ai_lambda_invoke_arn`, which must be the API Gateway invoke ARN of `var.ai_lambda_function_arn`. It must not be `aws_lambda_function.api.invoke_arn`.
- The stage resource is named `$default` with `auto_deploy = true`. The AI permission source is `${aws_apigatewayv2_api.http.execution_arn}/${aws_apigatewayv2_stage.default.name}/POST/ai/query`, which is the execution ARN plus `/$default/POST/ai/query`.
- Unqualified functions get statement `AllowApiGatewayInvokeAiQuery` with no qualifier. Qualified function ARNs get statement `AllowApiGatewayInvokeAiQueryQualified` and the alias or version qualifier. The principal is `apigateway.amazonaws.com` and the action is `lambda:InvokeFunction`.
- Operational GET routes stay on `aws_lambda_function.api` (`app.lambda_handler`). That function's permission source is `${aws_apigatewayv2_api.http.execution_arn}/*/*`. The AI function must not be `{name_prefix}-operational-api`. The operational log group `/aws/lambda/{name_prefix}-operational-api` is the only log group this module creates.
- Account and region are not literals. The HTTP API execution ARN is produced at apply time. A Lambda event cannot supply them.

`https://idp.wilvor.test/realms/ops` is a syntactic example used by tests. It is not a production issuer.

## Evidence classes

| Class | Meaning on this report |
| --- | --- |
| `VERIFIED_CONFIGURATION` | The checker accepted a field on the server-owned manifest. Internal consistency only. |
| `NOT_YET_INSPECTED` | A live AWS fact. Resource policy, identity policy, deployment, logs, qualifier target, and function URL stay here. |
| `UNTRUSTED_EVENT_FIELD` | A Lambda event, Lambda context, or trusted flag was presented and was not accepted as an invoke source. |

Empty account and region on the current disabled manifest stay `NOT_YET_INSPECTED` because the Terraform source does not hard-code them.

## Controls the integration owner still has to review

The checker copies this list onto every report. None of these items is satisfied by a passing syntactic check.

1. Confirm Terraform state and every tfvars file leave enable_ai_query_route false. The variable default in modules/operational_api/variables.tf is false, and this checker cannot see a deployed override.
2. Read the deployed HTTP API with apigatewayv2:GetApi and record its API id, account, region, and execution ARN. A Lambda event cannot supply those facts.
3. Confirm the deployed stage name is $default. The AI resource-policy source ARN must be the execution ARN plus /$default/POST/ai/query.
4. Confirm POST /ai/query is absent while the route is disabled. GET routes in local.route_keys stay on aws_lambda_function.api, whose permission source is the execution ARN plus /*/*. That wildcard must not be attached to the AI function.
5. When the route is enabled, confirm the JWT authorizer issuer and audience are the external identity provider values reviewed for this environment. This module creates neither an identity provider nor an audience. Do not take either value from the request, stage variables, or the event.
6. Confirm API Gateway validates the JWT signature, issuer, and audience at the edge. Signature validation still does not sign the Lambda event. This repository must not decode a JWT without verification.
7. Confirm modules/operational_api/api.tf sets no authorization_scopes on POST /ai/query. The current route does not enforce OAuth scopes. Scope strings in requestContext.authorizer.jwt are not a resource policy.
8. Confirm the AI integration is AWS_PROXY with payload format 2.0 and that its URI is the invoke ARN of the external AI function. The URI must not be aws_lambda_function.api.invoke_arn.
9. Confirm the AI function name is not {name_prefix}-operational-api, including aliases. The operational log group /aws/lambda/{name_prefix}-operational-api must not be the AI function log group.
10. Call lambda:GetPolicy on the AI function. Require one statement whose principal is apigateway.amazonaws.com, whose action is lambda:InvokeFunction, and whose source ARN is the execution ARN plus /$default/POST/ai/query. The qualifier must match the reviewed alias, or be empty. Reject any asterisk, any /*/* source, any other principal, and any second statement.
11. Review same-account identity policies for lambda:InvokeFunction on the AI function. The resource policy lets API Gateway invoke. It does not stop a same-account principal with an identity-based allow from invoking the function and submitting a forged HTTP API v2 event. Require no such allow, or an explicit Deny, before production issuance can be considered.
12. Confirm the AI function has no function URL and no EventBridge, SNS, S3, or cross-account invoke permission.
13. If the function ARN is qualified, resolve the alias or version and confirm it points at the reviewed code. This checker cannot see a later alias retarget.
14. Confirm the AI function log group exists, is distinct from the operational log group, and has a reviewed retention. This Terraform module creates a log group only for the operational function.
15. Use CloudTrail, not the event request id, before treating a past invoke as an API Gateway invoke. Lambda data events are not configured by this module.
16. Do not treat requestContext, authorizer.jwt, the Authorization header, Lambda context, or a trusted flag as invocation provenance. Production principal issuance remains unavailable. There is no setter for that decision in this module.

Issuance control ids, all unsatisfied:

- `resource_policy_scoped_to_post_ai_query`
- `no_wildcard_invoke_permission`
- `no_other_invoke_principal`
- `same_account_identity_policy_closed`
- `integration_targets_external_ai_lambda`
- `jwt_authorizer_issuer_and_audience`
- `stage_default_and_route_post_ai_query`
- `qualifier_resolved_to_reviewed_code`
- `operational_lambda_not_reused`
- `ai_log_group_separate_and_reviewed`
- `no_function_url_or_async_invoke_source`
- `live_deployment_inspected`

Before production issuance can be considered, the owner needs the live resource policy, the same-account identity policies, the integration URI, the JWT authorizer issuer and audience, the `$default` stage and `POST /ai/query` source ARN, the qualifier target, separation from the operational Lambda and its log group, and the absence of a function URL or other invoke source. Those checks happen in AWS. This Python module cannot complete them by reading an event.

## Cross-module interface

Baseline modules only. Do not import unmerged Sprint 3B.2 modules into this checker.

| Module | Boundary |
| --- | --- |
| `master_gateway_identity.assess_master_gateway_identity` | Structural parse of an HTTP API v2 document. Invocation provenance stays `NOT_ATTESTED`. Ingress attestation stays unresolved. |
| `master_gateway_identity.InvocationProvenanceStanding` | Shared standing. The only value is `NOT_ATTESTED`. This report uses that enum. |
| `PRODUCTION_GATEWAY_ISSUANCE_AVAILABLE` and `GATEWAY_INVOCATION_ATTESTATION_AVAILABLE` | Remain false. This module reads them and does not assign them. |
| `issue_application_principal` | Still fails. This module does not call it and does not define a replacement issuer. `refuse_runtime_admission` always fails. |
| `UnresolvedIngressAttestation` | Cannot be marked reviewed. A `RESTRICTED` report must not be treated as that review. |
| Shared envelope (`master_request_id`, `planning_session_id`, `step_id`, `invocation_id`, `specialist`, `purpose`, `capability`, `principal_ref`, `policy_revision`, `resource_scope`, `region_scope`, `historical_window`, `decision_target_scope`, `accepted_at_utc`, `deadline_utc`, `idempotency_key`, `max_model_turns`, `max_tool_calls`, `correlation_id`) | Not an authentication token. Not established here. Event values are not copied into these fields. |
| `MasterRouteExecutionAuthority`, `MasterProductionAuthority`, `MasterSourceAuthenticity`, `MasterRequestCoverage` | Stay `DENIED` or `NOT_ESTABLISHED`. |

`master_gateway_identity` parses claim shape with its own issuer grammar. This checker uses the Terraform issuer, function ARN, and invoke ARN patterns. Matching claim text is not signature verification, and a verified manifest field is not a verified invoke.

## Public checker

- `ServerOwnedGatewayManifest` and `InvokePermissionStatement`: server-owned description. A Lambda event dict is rejected.
- `current_disabled_route_manifest`: the current default, route off, account and region not inspected.
- `assess_gateway_runtime_boundary(manifest, event=None, *, lambda_context=None, trusted=None)`: deterministic configuration check. The three optional arguments never select issuer, audience, account, stage, or invocation source.
- `GatewayRuntimeBoundaryReport`: typed facts, denial reasons, unsatisfied issuance controls, and the checklist above. The report's boolean value is false.
- `refuse_runtime_admission`: always raises `runtime_admission_not_attested` and `production_issuance_unavailable`.

Denied configuration includes a disabled route, missing invoke permission, another invoke principal, a wildcard source or action, the operational Lambda or its log group, a mismatched issuer or audience, the wrong stage or route, and an account, region, partition, qualifier, function ARN, source ARN, or invoke ARN that does not match the manifest.

## What this review does not do

It does not plan or apply Terraform, change `enable_ai_query_route`, call AWS, decode a JWT, create a function URL, or issue an application principal. Git, merge, and deployment stay with the operator.
