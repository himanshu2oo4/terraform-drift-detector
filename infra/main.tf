resource "aws_s3_bucket" "state-bucket" {
  bucket = var.bucket_name

  tags = {
    Name      = var.bucket_name
    Owner     = var.owner
    ManagedBy = "terraform"
  }
}

# i dont want the versioning right now so i am commenting this block 
# resource "aws_s3_bucket_versioning" "demo" {
#   bucket = aws_s3_bucket.demo.id

#   versioning_configuration {
#     status = "Enabled"
#   }
# }