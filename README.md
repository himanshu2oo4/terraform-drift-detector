# Terraform Drift Detector

A  Python command-line tool that runs a Terraform plan, reports differences it finds in managed infrastructure, and can optionally send an email alert. For sample i included a S3 formation through terraform .

## Use cases

- **Spot out-of-band changes:** Find changes made directly in AWS that are not reflected in Terraform configuration.
- **Catch unexpected infrastructure changes:** Run checks manually or from an external scheduler/CI job and review the detector's exit code and logs. Scheduling is not included in this project.
- **Notify a team:** Send an email when drift is detected or the check fails.
- **Keep access limited:** The detector runs `terraform plan`; it does not run `terraform apply` or make infrastructure changes. Use AWS credentials with only the read access Terraform needs to inspect the resources.

## Requirements

- Python 3.10 or newer
- Terraform 1.5 or newer, available on `PATH`
- AWS credentials with appropriate read access to the resources being checked
- An initialized Terraform working directory with valid configuration and state
- For email alerts, SMTP account settings (a Gmail App Password can be used with Gmail)

## Setup

Run these commands from the project root.

1. Install the Python dependency:

   ```powershell
   python -m pip install python-dotenv
   ```

2. Set up the example Terraform configuration. Provide a globally unique bucket name in `infra\terraform.tfvars`:

   ```hcl
   bucket_name = "your-globally-unique-bucket-name"
   owner       = "devops-team"
   aws_region  = "ap-south-1"
   ```

   `bucket_name` is required; `owner` and `aws_region` have defaults. The `.tfvars` file is ignored by Git. Configure AWS credentials using your normal AWS credential provider, such as an AWS CLI profile or environment variables.

3. Initialize the Terraform directory:

   ```powershell
   terraform -chdir=infra init
   ```

4. To enable email alerts, copy `.env.sample` to `.env` and replace the example values with your SMTP settings. For Gmail, create an App Password and use it as `SMTP_PASSWORD`. `EMAIL_TO` can contain comma-separated recipients.

   ```powershell
   Copy-Item .env.sample .env
   ```

   Keep `.env` and any real credentials private; do not commit them.

## Run

From the project root, run a check without email:

```powershell
python detector\drift_detector.py --infra-dir infra
```

Send an email when drift is found or the check encounters an error:

```powershell
python detector\drift_detector.py --infra-dir infra --notify
```

Attach the latest detector logs to that email:

```powershell
python detector\drift_detector.py --infra-dir infra --notify --attach-logs
```

`--attach-logs` is intended to be used with `--notify`. Logs may include infrastructure values; only attach or forward them when appropriate.

## Results and exit codes

The detector uses Terraform's `plan -detailed-exitcode` result:

| Exit code | Meaning |
| --- | -----------------------------------------------------------------------------------------------------------------------|
| `0` | No changes found; the infrastructure matches the Terraform plan.                                                       |
| `1` | The check failed, for example because Terraform, credentials, or configuration are unavailable or invalid.             |
| `2` | Terraform found differences. These can indicate out-of-band drift or unapplied changes in the Terraform configuration; |
              review the log output to distinguish them.                                                                             |

In PowerShell, inspect the last command's exit code with:

```powershell
$LASTEXITCODE
```

In Linux or macOS shells, use:

```sh
echo $?
```

The detector reports drifted resource addresses and changed attributes in its logs. Email alerts list resource names and changed setting names, not their values.

