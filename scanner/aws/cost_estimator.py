import boto3
import json
import os
import threading
from utils.logger import get_logger
import locale
import time

# Set locale for currency formatting
locale.setlocale(locale.LC_ALL, 'en_US.UTF-8')
logger = get_logger(__name__)

class CostEstimator:
    """
    AWS Cost Estimator that calculates the cost of resources based on live AWS pricing.
    This version includes caching of price data to a local .json file with thread-safety.
    """

    def __init__(self, cache_file="cost_estimator.json"):
        self.pricing_client = boto3.client('pricing', region_name="us-east-1")
        self.cache_file = cache_file
        self.price_cache = self._load_cache()
        self.cache_lock = threading.Lock()  # Lock to ensure thread-safe cache access
        self.save_lock = threading.Lock()  # Separate lock for saving cache
        logger.debug(f"Initialized CostEstimator with cache file: {self.cache_file}")

    def _load_cache(self):
        """Loads the cached pricing data from the JSON file."""
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, 'r') as f:
                    cache_data = json.load(f)
                logger.debug(f"Loaded price cache from {self.cache_file}.")
                return cache_data
            except Exception as e:
                logger.error(f"Error loading price cache: {e}")
                return {}
        return {}

    def _save_cache(self):
        """Saves the pricing cache to the JSON file, thread-safely."""
        try:
            logger.debug("Attempting to acquire cache save lock.")
            with self.save_lock:  # Use a separate lock for saving cache
                with open(self.cache_file, 'w') as f:
                    json.dump(self.price_cache, f, indent=4)
                logger.info(f"Price cache saved to {self.cache_file}.")
        except Exception as e:
            logger.error(f"Error saving price cache: {e}")

    def _get_aws_price(self, service_code, price_filters):
        """
        Retrieves the price for a specific AWS service and attributes from AWS Pricing.
        First checks the cache, if not available, fetches from AWS Pricing API.
        Uses thread-safe access to cache.
        """
        filters_str = json.dumps(price_filters, sort_keys=True)
        cache_key = f"{service_code}_{filters_str}"

        if cache_key in self.price_cache:
            logger.debug(f"Cache hit for {service_code} with filters {price_filters}.")
            return self.price_cache[cache_key]

        logger.debug(f"Cache miss for {service_code} with filters {price_filters}. Fetching from AWS Pricing API.")
        try:
            filters = [{"Type": "TERM_MATCH", "Field": key, "Value": value} for key, value in price_filters.items()]
            response = self.pricing_client.get_products(ServiceCode=service_code, Filters=filters)
            logger.debug(f"API Response for {service_code} with filters {price_filters}: {response}")

            price_list = response.get("PriceList", [])
            if not price_list:
                logger.warning(f"No pricing information found for {service_code} with filters {price_filters}.")
                return None

            pricing_data = json.loads(price_list[0])
            on_demand_terms = pricing_data["terms"]["OnDemand"]
            term_key = list(on_demand_terms.keys())[0]
            price_dimensions = on_demand_terms[term_key]["priceDimensions"]
            price_key = list(price_dimensions.keys())[0]
            price_per_unit = float(price_dimensions[price_key]["pricePerUnit"]["USD"])

            # Validate that price is not zero or zero-like
            if price_per_unit == 0 or price_per_unit < 0.01:
                logger.warning(f"Received invalid price for {service_code} with filters {price_filters}: {price_per_unit}.")
                return None

            # Update cache with the valid price
            logger.debug(f"Updating cache for {service_code} with filters {price_filters}.")
            with self.cache_lock:
                self.price_cache[cache_key] = price_per_unit
            self._save_cache()

            return price_per_unit

        except Exception as error:
            logger.error(f"Error retrieving pricing for {service_code}: {error}")
            return None

    def calculate_cost(self, resource_type, resource_size=None, hours_running=0):
        """
        Calculates the cost for a given resource type, size, and running duration.
        """
        # Map resource types to AWS service codes
        service_code_map = {
            "EBS-Volumes": "AmazonEC2",  # Correct service code for EBS Volumes
            "EC2": "AmazonEC2",
            "EBS-Snapshots": "AmazonEC2",  # Correct service code for EBS Snapshots
            "RDS": "AmazonRDS",
            "DynamoDB": "AmazonDynamoDB",
            "EIP": "AmazonEC2",
            "LoadBalancer": "ElasticLoadBalancing",
        }

        # Attribute filters for pricing queries
        price_filter_map = {
            "EBS-Volumes": {"productFamily": "Storage", "volumeType": "General Purpose"},
            "EC2": {"productFamily": "Compute Instance", "instanceType": resource_size},
            "EBS-Snapshots": {"productFamily": "Storage Snapshot"},
            "RDS": {"productFamily": "Database Instance", "instanceType": resource_size},
            "DynamoDB": {"productFamily": "Non-relational Database"},
            "EIP": {"productFamily": "IP Address"},
            "LoadBalancer": {"productFamily": "Load Balancer"},
        }

        service_code = service_code_map.get(resource_type)
        if not service_code:
            raise ValueError(f"Unsupported resource type: {resource_type}")

        price_filters = price_filter_map.get(resource_type)
        if not price_filters:
            raise ValueError(f"Attribute filters not defined for resource type: {resource_type}")

        # Fetch price per hour from AWS Pricing API or cache
        price_per_hour = self._get_aws_price(service_code, price_filters)
        if price_per_hour is None:
            logger.warning(f"Could not calculate cost for {resource_type} of size {resource_size}.")
            return None

        # Calculate costs
        cost_per_hour = price_per_hour
        cost_per_day = cost_per_hour * 24
        cost_per_month = cost_per_day * 30
        cost_per_year = cost_per_day * 365
        lifetime_cost = cost_per_hour * hours_running  # Lifetime cost

        # Format costs in currency
        def format_currency(amount):
            return f"${amount:,.2f}"

        return {
            "hourly": format_currency(cost_per_hour),
            "daily": format_currency(cost_per_day),
            "monthly": format_currency(cost_per_month),
            "yearly": format_currency(cost_per_year),
            "lifetime": format_currency(lifetime_cost),
        }