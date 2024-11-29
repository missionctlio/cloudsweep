from datetime import datetime, timezone, timedelta
from utils.logger import get_logger
from config.config import DAYS_THRESHOLD
from scanner.resource_scanner_registry import ResourceScannerRegistry

logger = get_logger(__name__)

class EbsVolumeScanner(ResourceScannerRegistry):
    """
    Scanner for EBS Volumes.
    """
    argument_name = "ebs-volumes"
    label = "EBS Volumes"

    def __init__(self):
        super().__init__(name=__name__, argument_name=self.argument_name, label=self.label)

    def scan(self, session, *args, **kwargs):
        """Retrieve EBS volumes and check for unused volumes."""
        logger.debug("Retrieving EBS volumes...")
        try:
            ec2_client = session.get_client("ec2")
            volumes = ec2_client.describe_volumes()["Volumes"]
            unused_volumes = []
            current_time = datetime.now(timezone.utc)

            for volume in volumes:
                volume_id = volume["VolumeId"]
                logger.debug(f"Checking EBS volume {volume_id} for usage...")

                # Retrieve volume name (if tagged)
                volume_name = "Unnamed"
                if "Tags" in volume:
                    for tag in volume["Tags"]:
                        if tag["Key"] == "Name":
                            volume_name = tag["Value"]
                            break

                # Check if the volume is unattached
                if not volume["Attachments"]:
                    create_time = volume["CreateTime"]
                    days_since_creation = (current_time - create_time).days

                    # Mark volume as unused if it's older than the threshold
                    if days_since_creation >= DAYS_THRESHOLD:
                        unused_volumes.append({
                            "Name": volume_name,
                            "VolumeId": volume_id,
                            "State": volume["State"],
                            "Size": volume["Size"],  # Size in GiB
                            "CreateTime": create_time,
                            "AccountId": session.account_id,
                            "Reason": f"Volume has been unattached for {days_since_creation} days, exceeding the threshold of {DAYS_THRESHOLD} days"
                        })
                        logger.info(f"EBS volume {volume_id} ({volume_name}) is unused.")

            logger.info(f"Found {len(unused_volumes)} unused EBS volumes.")
            return unused_volumes

        except Exception as e:
            logger.error(f"Error retrieving EBS volumes: {e}")
            return []

