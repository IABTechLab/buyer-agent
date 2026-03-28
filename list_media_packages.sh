#!/bin/bash
# List available media packages from seller agents using curl

# Get seller endpoints from environment or use default
if [ -z "$SELLER_ENDPOINTS" ]; then
    SELLER_ENDPOINTS="http://localhost:8001"
    echo "No SELLER_ENDPOINTS configured. Using default: $SELLER_ENDPOINTS"
    echo "Set SELLER_ENDPOINTS environment variable to specify seller URLs."
    echo ""
fi

# Get max price filter from environment
MAX_PRICE="${MAX_PRICE:-}"
if [ -n "$MAX_PRICE" ]; then
    echo "Filtering packages with minimum price under \$$MAX_PRICE CPM"
    echo ""
fi

echo "Querying seller agent(s) for media packages..."
echo ""

# Split by comma and process each seller
for seller_url in $(echo "$SELLER_ENDPOINTS" | tr ',' ' '); do
    seller_url=$(echo "$seller_url" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')  # trim whitespace
    packages_url="${seller_url%/}/media-kit/packages"
    
    echo "============================================================"
    echo "Seller: $seller_url"
    echo "============================================================"
    echo ""
    
    # Try to fetch packages
    response=$(curl -s -w "\n%{http_code}" "$packages_url" 2>&1)
    http_code=$(echo "$response" | tail -n1)
    body=$(echo "$response" | sed '$d')
    
    if [ "$http_code" != "200" ]; then
        echo "  Error: HTTP $http_code"
        echo "  URL: $packages_url"
        echo ""
        continue
    fi
    
    # Parse JSON and display packages nicely
    package_count=$(echo "$body" | python3 -c "import sys, json; data=json.load(sys.stdin); print(len(data.get('packages', [])))" 2>/dev/null || echo "0")
    
    if [ "$package_count" = "0" ] || [ -z "$package_count" ]; then
        echo "  No packages found."
        echo ""
        continue
    fi
    
    echo "  Found $package_count package(s):"
    echo ""
    
    # Display each package with optional price filtering
    echo "$body" | python3 -c "
import sys, json
import re
import os

max_price = os.getenv('MAX_PRICE')
max_price_float = float(max_price) if max_price and max_price.replace('.', '').isdigit() else None

try:
    data = json.load(sys.stdin)
    packages = data.get('packages', [])
    filtered_packages = []
    
    for pkg in packages:
        # Filter by price if MAX_PRICE is set
        if max_price_float is not None:
            price_range = pkg.get('price_range', '')
            # Extract minimum price from range like \"\$19-\$29 CPM\" or \"\$16-\$24 CPM\"
            match = re.search(r'[\$](\d+(?:\.\d+)?)', price_range)
            if match:
                min_price = float(match.group(1))
                if min_price >= max_price_float:
                    continue  # Skip packages with min price >= max_price
            else:
                # If we can't parse the price, skip it when filtering
                continue
        
        filtered_packages.append(pkg)
    
    if max_price_float is not None and len(filtered_packages) < len(packages):
        print(f'  Filtered to {len(filtered_packages)} package(s) under \${max_price_float} CPM:')
        print()
    
    for i, pkg in enumerate(filtered_packages, 1):
        print(f'  [{i}] {pkg.get(\"name\", \"Unnamed Package\")}')
        print(f'      ID: {pkg.get(\"package_id\", \"N/A\")}')
        if pkg.get('description'):
            desc = pkg['description']
            if len(desc) > 80:
                desc = desc[:77] + '...'
            print(f'      Description: {desc}')
        if pkg.get('price_range'):
            print(f'      Price: {pkg[\"price_range\"]}')
        if pkg.get('ad_formats'):
            print(f'      Formats: {\", \".join(pkg[\"ad_formats\"])}')
        if pkg.get('device_types'):
            devices = [str(d) for d in pkg['device_types']]
            print(f'      Devices: {\", \".join(devices)}')
        if pkg.get('cat'):
            print(f'      Categories: {\", \".join(pkg[\"cat\"])}')
        if pkg.get('tags'):
            print(f'      Tags: {\", \".join(pkg[\"tags\"])}')
        if pkg.get('is_featured'):
            print(f'      ⭐ Featured')
        print()
except Exception as e:
    print(f'  Error parsing response: {e}')
    sys.exit(1)
" 2>/dev/null || {
        echo "  Packages found (raw JSON):"
        echo "$body" | python3 -m json.tool 2>/dev/null | head -100
    }
    echo ""
done

# Calculate totals
total_packages=0
for seller_url in $(echo "$SELLER_ENDPOINTS" | tr ',' ' '); do
    seller_url=$(echo "$seller_url" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
    packages_url="${seller_url%/}/media-kit/packages"
    response=$(curl -s -w "\n%{http_code}" "$packages_url" 2>&1)
    http_code=$(echo "$response" | tail -n1)
    if [ "$http_code" = "200" ]; then
        body=$(echo "$response" | sed '$d')
        count=$(echo "$body" | python3 -c "import sys, json; data=json.load(sys.stdin); print(len(data.get('packages', [])))" 2>/dev/null || echo "0")
        total_packages=$((total_packages + count))
    fi
done

seller_count=$(echo "$SELLER_ENDPOINTS" | tr ',' '\n' | grep -c . || echo "1")

echo "============================================================"
echo "Summary: $total_packages package(s) across $seller_count seller(s)"
echo "============================================================"
