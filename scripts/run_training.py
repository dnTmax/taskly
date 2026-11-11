#!/usr/bin/env python3
"""Lance l'entrainement du modele sur une instance spot, via SSM, puis supprime l'instance.

Usage : run_training.py --bucket B --subnet S --template T --mlflow-uri http://IP:30500
"""
import argparse
import sys
import tarfile
import time

import boto3
from botocore.exceptions import ClientError

REGION = "us-east-1"
ACTIVE = ("Pending", "InProgress", "Delayed")


class Interrupted(Exception):
    """L'instance spot a disparu avant la fin de l'entrainement."""


def upload_sources(s3, bucket, key):
    with tarfile.open("/tmp/src.tgz", "w:gz") as tar:
        tar.add("model")
        tar.add("training")
    s3.upload_file("/tmp/src.tgz", bucket, key)


def state(ec2, iid):
    r = ec2.describe_instances(InstanceIds=[iid])["Reservations"][0]["Instances"][0]
    return r["State"]["Name"]


def wait_online(ec2, ssm, iid, timeout=420):
    end = time.time() + timeout
    while time.time() < end:
        if state(ec2, iid) in ("shutting-down", "terminated"):
            raise Interrupted("instance supprimee avant d'etre joignable")
        info = ssm.describe_instance_information(
            Filters=[{"Key": "InstanceIds", "Values": [iid]}]
        )["InstanceInformationList"]
        if info and info[0]["PingStatus"] == "Online":
            return
        time.sleep(10)
    raise RuntimeError("l'agent SSM ne s'est pas declare a temps")


def run_commands(ec2, ssm, iid, commands, timeout=1800):
    cid = ssm.send_command(
        InstanceIds=[iid],
        DocumentName="AWS-RunShellScript",
        Parameters={"commands": commands, "executionTimeout": [str(timeout)]},
    )["Command"]["CommandId"]
    end = time.time() + timeout
    while time.time() < end:
        time.sleep(5)
        if state(ec2, iid) in ("shutting-down", "terminated"):
            raise Interrupted("instance reprise par AWS pendant l'entrainement")
        try:
            r = ssm.get_command_invocation(CommandId=cid, InstanceId=iid)
        except ssm.exceptions.InvocationDoesNotExist:
            continue
        if r["Status"] not in ACTIVE:
            print(r["StandardOutputContent"])
            if r["StandardErrorContent"]:
                print(r["StandardErrorContent"], file=sys.stderr)
            return r["Status"]
    raise RuntimeError("delai depasse")


TYPES = ["t3.large", "t3a.large", "m5.large", "m6i.large"]
CAPACITY = ("InsufficientInstanceCapacity", "SpotMaxPriceTooLow",
            "MaxSpotInstanceCountExceeded", "Unsupported",
            "InsufficientFreeAddressesInSubnet")


def launch_spot(ec2, template, subnet):
    """Essaie plusieurs types de machines et les deux zones, jusqu'a obtenir du spot."""
    vpc = ec2.describe_subnets(SubnetIds=[subnet])["Subnets"][0]["VpcId"]
    subs = ec2.describe_subnets(Filters=[
        {"Name": "vpc-id", "Values": [vpc]},
        {"Name": "tag:Name", "Values": ["*private*"]},
    ])["Subnets"]
    ids = [subnet] + [x["SubnetId"] for x in subs if x["SubnetId"] != subnet]
    last = None
    for itype in TYPES:
        for sid in ids:
            try:
                r = ec2.run_instances(
                    LaunchTemplate={"LaunchTemplateName": template},
                    InstanceType=itype, SubnetId=sid, MinCount=1, MaxCount=1,
                )
                print(f"spot obtenu : {itype} dans {sid}")
                return r["Instances"][0]["InstanceId"]
            except ClientError as e:
                code = e.response["Error"]["Code"]
                if code not in CAPACITY:
                    raise
                print(f"pas de capacite spot : {itype} dans {sid} ({code})", file=sys.stderr)
                last = e
    raise last


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--bucket", required=True)
    p.add_argument("--subnet", required=True)
    p.add_argument("--template", required=True)
    p.add_argument("--mlflow-uri", required=True)
    p.add_argument("--attempts", type=int, default=2)
    a = p.parse_args()

    ec2 = boto3.client("ec2", region_name=REGION)
    ssm = boto3.client("ssm", region_name=REGION)
    s3 = boto3.client("s3", region_name=REGION)

    key = "training/src.tgz"
    upload_sources(s3, a.bucket, key)

    commands = [
        f"curl -sf -m 5 {a.mlflow_uri}/health > /dev/null || (echo 'MLflow injoignable' && exit 1)",
        f"aws s3 cp s3://{a.bucket}/{key} /tmp/src.tgz --only-show-errors",
        "mkdir -p /opt/train && tar -xzf /tmp/src.tgz -C /opt/train",
        f"cd /opt/train && MLFLOW_TRACKING_URI={a.mlflow_uri} bash training/remote_train.sh",
    ]

    for attempt in range(1, a.attempts + 1):
        iid = launch_spot(ec2, a.template, a.subnet)
        print(f"[tentative {attempt}/{a.attempts}] instance spot : {iid}")
        try:
            wait_online(ec2, ssm, iid)
            status = run_commands(ec2, ssm, iid, commands)
            if status == "Success":
                print("Entrainement reussi")
                return 0
            print(f"Echec de l'entrainement (statut {status})", file=sys.stderr)
            return 1
        except Interrupted as e:
            print(f"Interruption : {e}", file=sys.stderr)
        finally:
            ec2.terminate_instances(InstanceIds=[iid])
            print(f"instance {iid} supprimee")
    print("Entrainement impossible apres plusieurs interruptions", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
