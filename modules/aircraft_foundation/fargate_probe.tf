data "aws_availability_zones" "available" {
  state = "available"
}

resource "aws_ecr_repository" "opensky_fargate_probe" {
  name         = "${var.name_prefix}-opensky-fargate-probe"
  force_delete = true

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = merge(var.tags, {
    Name      = "${var.name_prefix}-opensky-fargate-probe"
    Component = "aircraft-ingestion"
    Purpose   = "opensky-fargate-connectivity-probe"
  })
}

resource "aws_vpc" "opensky_fargate_probe" {
  cidr_block           = "10.91.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = merge(var.tags, {
    Name      = "${var.name_prefix}-opensky-fargate-probe-vpc"
    Component = "aircraft-ingestion"
    Purpose   = "opensky-fargate-connectivity-probe"
  })
}

resource "aws_internet_gateway" "opensky_fargate_probe" {
  vpc_id = aws_vpc.opensky_fargate_probe.id

  tags = merge(var.tags, {
    Name      = "${var.name_prefix}-opensky-fargate-probe-igw"
    Component = "aircraft-ingestion"
    Purpose   = "opensky-fargate-connectivity-probe"
  })
}

resource "aws_subnet" "opensky_fargate_probe_public" {
  vpc_id                  = aws_vpc.opensky_fargate_probe.id
  cidr_block              = "10.91.1.0/24"
  availability_zone       = data.aws_availability_zones.available.names[0]
  map_public_ip_on_launch = true

  tags = merge(var.tags, {
    Name      = "${var.name_prefix}-opensky-fargate-probe-public-subnet"
    Component = "aircraft-ingestion"
    Purpose   = "opensky-fargate-connectivity-probe"
  })
}

resource "aws_route_table" "opensky_fargate_probe_public" {
  vpc_id = aws_vpc.opensky_fargate_probe.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.opensky_fargate_probe.id
  }

  tags = merge(var.tags, {
    Name      = "${var.name_prefix}-opensky-fargate-probe-public-rt"
    Component = "aircraft-ingestion"
    Purpose   = "opensky-fargate-connectivity-probe"
  })
}

resource "aws_route_table_association" "opensky_fargate_probe_public" {
  subnet_id      = aws_subnet.opensky_fargate_probe_public.id
  route_table_id = aws_route_table.opensky_fargate_probe_public.id
}

resource "aws_security_group" "opensky_fargate_probe" {
  name        = "${var.name_prefix}-opensky-fargate-probe-sg"
  description = "Security group for OpenSky Fargate connectivity probe"
  vpc_id      = aws_vpc.opensky_fargate_probe.id

  egress {
    description = "Allow outbound internet traffic"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(var.tags, {
    Name      = "${var.name_prefix}-opensky-fargate-probe-sg"
    Component = "aircraft-ingestion"
    Purpose   = "opensky-fargate-connectivity-probe"
  })
}

resource "aws_cloudwatch_log_group" "opensky_fargate_probe" {
  name              = "/ecs/${var.name_prefix}-opensky-fargate-probe"
  retention_in_days = 1

  tags = merge(var.tags, {
    Name      = "/ecs/${var.name_prefix}-opensky-fargate-probe"
    Component = "aircraft-ingestion"
    Purpose   = "opensky-fargate-connectivity-probe"
  })
}

resource "aws_ecs_cluster" "opensky_fargate_probe" {
  name = "${var.name_prefix}-opensky-fargate-probe"

  tags = merge(var.tags, {
    Name      = "${var.name_prefix}-opensky-fargate-probe"
    Component = "aircraft-ingestion"
    Purpose   = "opensky-fargate-connectivity-probe"
  })
}

data "aws_iam_policy_document" "ecs_tasks_assume_role" {
  statement {
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }

    actions = ["sts:AssumeRole"]
  }
}

resource "aws_iam_role" "opensky_fargate_probe_execution" {
  name               = "${var.name_prefix}-opensky-fargate-probe-execution-role"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume_role.json

  tags = merge(var.tags, {
    Name      = "${var.name_prefix}-opensky-fargate-probe-execution-role"
    Component = "aircraft-ingestion"
    Purpose   = "opensky-fargate-connectivity-probe"
  })
}

resource "aws_iam_role_policy_attachment" "opensky_fargate_probe_execution" {
  role       = aws_iam_role.opensky_fargate_probe_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role" "opensky_fargate_probe_task" {
  name               = "${var.name_prefix}-opensky-fargate-probe-task-role"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume_role.json

  tags = merge(var.tags, {
    Name      = "${var.name_prefix}-opensky-fargate-probe-task-role"
    Component = "aircraft-ingestion"
    Purpose   = "opensky-fargate-connectivity-probe"
  })
}

data "aws_iam_policy_document" "opensky_fargate_probe_task" {
  statement {
    sid    = "ReadOpenSkyCredentials"
    effect = "Allow"

    actions = [
      "secretsmanager:GetSecretValue"
    ]

    resources = [
      aws_secretsmanager_secret.opensky_credentials.arn
    ]
  }
}

resource "aws_iam_role_policy" "opensky_fargate_probe_task" {
  name   = "${var.name_prefix}-opensky-fargate-probe-task-policy"
  role   = aws_iam_role.opensky_fargate_probe_task.id
  policy = data.aws_iam_policy_document.opensky_fargate_probe_task.json
}

resource "aws_ecs_task_definition" "opensky_fargate_probe" {
  family                   = "${var.name_prefix}-opensky-fargate-probe"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"

  cpu    = "256"
  memory = "512"

  execution_role_arn = aws_iam_role.opensky_fargate_probe_execution.arn
  task_role_arn      = aws_iam_role.opensky_fargate_probe_task.arn

  container_definitions = jsonencode([
    {
      name      = "opensky-fargate-probe"
      image     = "${aws_ecr_repository.opensky_fargate_probe.repository_url}:latest"
      essential = true

      environment = [
        {
          name  = "AWS_REGION"
          value = var.aws_region
        },
        {
          name  = "OPENSKY_SECRET_ARN"
          value = aws_secretsmanager_secret.opensky_credentials.arn
        },
        {
          name  = "OPENSKY_TOKEN_URL"
          value = "https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token"
        },
        {
          name  = "OPENSKY_STATES_URL"
          value = "https://opensky-network.org/api/states/all"
        },
        {
          name  = "OPENSKY_LAMIN"
          value = "37.0"
        },
        {
          name  = "OPENSKY_LOMIN"
          value = "-123.0"
        },
        {
          name  = "OPENSKY_LAMAX"
          value = "38.5"
        },
        {
          name  = "OPENSKY_LOMAX"
          value = "-121.5"
        }
      ]

      logConfiguration = {
        logDriver = "awslogs"

        options = {
          awslogs-group         = aws_cloudwatch_log_group.opensky_fargate_probe.name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "ecs"
        }
      }
    }
  ])

  tags = merge(var.tags, {
    Name      = "${var.name_prefix}-opensky-fargate-probe"
    Component = "aircraft-ingestion"
    Purpose   = "opensky-fargate-connectivity-probe"
  })
}