# Decision log

English | [Français](DECISIONS.fr.md)

Each decision states what was chosen, why, and what it costs. The last sections list the
problems met during the project and the known limits.

## 1. Why spot instances for training

**Decision.** The model is trained on an EC2 spot instance, launched only for the training and
terminated right after.

**Why.** Training is short (about 4 minutes), restartable and holds no state worth keeping, which
is exactly the kind of job spot capacity suits: spot instances are much cheaper than on-demand
ones, and nothing runs (or is billed) between two trainings. The instance is also never left
running by mistake, because the launcher script terminates it in all cases.

**Trade-off.** AWS can refuse a spot request (no capacity) or take the instance back at any time.

## 2. What happens if the training is interrupted

| Situation | What the system does |
|---|---|
| No spot capacity in a zone (seen for real, `InsufficientInstanceCapacity`) | The launcher tries the second private subnet, then other instance types (`t3a.large`, `m5.large`, `m6i.large`) |
| The spot instance is reclaimed during training | The launcher detects that the instance is gone and starts a new one, for a total of 2 attempts (`--attempts 2`) |
| Any failure or interruption | The instance is always terminated (`finally` block), so nothing keeps billing |
| All attempts fail | The pipeline fails before the promotion step: staging and production are not touched |

Why an interruption cannot damage production: the model is registered in MLflow only at the very
end of the training, and a new version only reaches `staging` after passing the validation rule,
then `production` after a human approval.

Known limits: an interrupted run may stay displayed as `RUNNING` in MLflow tracking (an orphan
run, harmless for the registry); there is no checkpointing, because restarting a 4-minute
training costs less than building checkpoints; there is no automatic fallback to an on-demand
instance yet (a natural next step if spot capacity stays scarce).

## 3. Design decisions

| # | Decision | Why | Trade-off |
|---|---|---|---|
| 1 | Training instance driven through SSM, no SSH | No open port, no key to store, every command is traced by AWS | Needs the SSM agent and an instance role |
| 2 | Temporary Postgres in Docker on the training instance, random password, bound to 127.0.0.1 | The training code reads its data from Postgres; the database disappears with the instance | Data is regenerated at each run (synthetic data) |
| 3 | MLflow and Postgres inside the cluster rather than managed services | Lower cost, simple to rebuild | Single replica, no high availability, no automatic backup |
| 4 | MLflow artifact proxy mode (`--serve-artifacts`) with S3 | Clients (training, API) need no S3 credentials; only the MLflow server has an IAM role (IRSA) | All artifact traffic goes through the server |
| 5 | MLflow aliases (`staging`, `production`) instead of stages | Stages are deprecated; aliases are flexible and visible in the UI | Aliases must be moved explicitly |
| 6 | The API loads the model at startup, from the registry, by alias resolved to a version number | The model is never baked into the image; switching a version needs no manifest change; no race if the alias moves during loading | A restart is needed to pick up a new version (done by the rolling update) |
| 7 | One Kustomize base and two overlays, one namespace per environment | Same manifests for both environments, only the alias, replica count and alert differ | One more tool to know |
| 8 | Deterministic promotion rule (F1 macro at least 0.70 and not more than 0.02 below production) | Predictable, testable, auditable; the decision is never delegated to a model | Thresholds must be tuned with real data |
| 9 | Secrets created at deploy time from random values, never in Git | Nothing sensitive in the repository | Kubernetes Secrets are only base64 encoded; EKS envelope encryption with KMS is not enabled |
| 10 | GitHub Actions authenticates to AWS with OIDC | No long-lived key in GitHub | The trust policy must match GitHub's identity format (see problems) |
| 11 | Least privilege for the pipeline role | Termination is limited to instances tagged with the project, and the role passed to the instance is limited to the training role | `RunInstances`, `SendCommand` and `CreateTags` stay on `*`, because AWS does not allow finer scoping |
| 12 | Same pinned dependency versions for training and serving | A model must load with the library versions that created it | Versions must be updated together |
| 13 | Rolling update with `maxUnavailable: 0`, a readiness probe, a PodDisruptionBudget and a `preStop` pause of 10 seconds | No dropped request during an update, measured (see problems) | A pod takes 10 seconds longer to stop |
| 14 | Fault injection with `SIMULATE_ERROR_RATE` | Lets the 5 % alert be tested on demand | Must be removed after the test |
| 15 | Alert only in production | Staging errors are expected and must not page anyone | Staging has no alert |
| 16 | One NAT gateway, no load balancer, port-forward and a restricted NodePort for access | Lower cost for a learning environment | Not highly available; not for real production |

## 4. About the data

The dataset is synthetic (generated by the repository code), so an accuracy of 1.0 only means
the classes are easy to separate. It is not a claim about real-world performance, and the
validation thresholds would need to be tuned on real data.

## 5. Problems met and how they were solved

| Problem | Cause | Solution |
|---|---|---|
| GitHub could not assume the AWS role (`Not authorized to perform sts:AssumeRoleWithWebIdentity`) | GitHub now sends the identity as `repo:owner@id/repo@id:...` (with immutable numeric IDs) instead of `repo:owner/repo:...`; IDs protect against someone re-registering a deleted or renamed name | A debug workflow printed the real claims; the trust policy now allows both formats (`github_repo_sub`). Already met on an earlier project |
| "Zero downtime" update dropped 9 requests out of 2021 (0.45 %) | A stopping pod closed its port before the service removed it from rotation | `preStop` pause of 10 seconds; the same load test then gave 0 errors out of 2011 |
| Spot capacity unavailable | Normal spot behavior | Multi-zone and multi-type fallback, observed again inside the real pipeline |
| Spot instance vanished by itself | Spot instances can be reclaimed | Interruption handling and unconditional termination |
| `InvalidInstanceID.NotFound` right after launching an instance | AWS needs a few seconds to propagate a new instance | The launcher treats it as "pending" |
| Pipeline could not list buckets | Least privilege on purpose | The bucket name is computed from the account number, no extra permission |
| Autoscaler showing `cpu: unknown`, and a namespace stuck in `Terminating` for a day | The EKS managed metrics-server uses port 10251, which the node security group did not allow | Security group rule in Terraform; spotted thanks to the `TARGETS` column of the autoscaler |
| Duplicate Terraform resource for the pipeline permissions | The permissions already existed in another file | Removed the duplicate; only `ec2:DescribeSubnets` was missing |
| AWS CLI 2.31 on Python 3.14: "badly formed help string" | Python 3.14 argparse rejects a `%` in the help text | Calls made with boto3 on Python 3.12 |
| Security group rule refused | An apostrophe is not allowed in a description | Description rewritten (met twice) |
| MLflow crash loop, then out of memory | SQLAlchemy incompatibility; too many workers | SQLAlchemy pinned to 2.0.36; 2 workers and 1 GiB |
| Mixed log formats | The API already wrote JSON with the model version, but uvicorn wrote a plain text access log (health probes) | One JSON line per request from the API, and uvicorn started with `--no-access-log` |

## 6. Next steps

Fallback to an on-demand instance when no spot capacity is found; an AI validation agent that
reads training metrics before the promotion (the deterministic rule would stay the final gate);
encryption of Kubernetes Secrets with KMS; backups for Postgres; real training data.

---

Dally NTOLO, DevOps & Cloud
