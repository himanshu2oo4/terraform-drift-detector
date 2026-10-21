variable "aws_region" {
  description = "AWS region where resources are created"
  type        = string
  default     = "ap-south-1"
}

variable "bucket_name" {
  description = "Globally unique S3 bucket name"
  type        = string
}

variable "owner" {
  description = "Team or person responsible for these resources"
  type        = string
  default     = "devops-team"
}