#!/bin/bash
set -e

# Setup User and Group dynamically based on env vars
USER_ID=${PUID:-1000}
GROUP_ID=${PGID:-1000}

if ! getent group "$GROUP_ID" >/dev/null; then
    groupadd -g "$GROUP_ID" appgroup
fi

if ! getent passwd "$USER_ID" >/dev/null; then
    useradd -u "$USER_ID" -g "$GROUP_ID" -m -s /bin/bash appuser
fi

# ---------------------------------------------------------
# 1. HARDWARE DISCOVERY
# ---------------------------------------------------------
echo "Searching for GoPro hardware..."
GOPRO_IFACE=""
for iface in /sys/class/net/*; do
    # USB network adapters have their Vendor ID one directory up from 'device'
    VENDOR_FILE="$iface/device/../idVendor"
    
    if [ -f "$VENDOR_FILE" ] && [ "$(cat "$VENDOR_FILE")" == "2672" ]; then
        GOPRO_IFACE=$(basename "$iface")
        break
    fi
done

if [ -z "$GOPRO_IFACE" ]; then
    echo "ERROR: No GoPro detected."
    exit 1
fi
echo "Found GoPro on interface: $GOPRO_IFACE"

# ---------------------------------------------------------
# 2. DYNAMIC IP CALCULATION
# ---------------------------------------------------------
SERIAL=$(cat "/sys/class/net/$GOPRO_IFACE/device/../serial")
echo "GoPro Serial: $SERIAL"
# We dynamically inject the serial number for Pydantic
export SCREEN__GOPRO__SERIAL_NUMBER=$SERIAL

# Extract last 3 digits for math
X=${SERIAL: -3:1}
Y=${SERIAL: -2:1}
Z=${SERIAL: -1:1}

# Calculate exact octets
OCTET2=$((20 + X))
OCTET3=$((100 + (Y * 10) + Z))

GOPRO_IP="172.${OCTET2}.${OCTET3}.51"
HOST_IP="172.${OCTET2}.${OCTET3}.50"
SUBNET="172.${OCTET2}.${OCTET3}.0/24"

# ---------------------------------------------------------
# 3. COLLISION DETECTION
# ---------------------------------------------------------
# Check if the calculated subnet is already in use by ANOTHER interface
if ip route show | grep -q "$SUBNET" && ! ip route show | grep "$SUBNET" | grep -q "$GOPRO_IFACE"; then
    echo "ERROR: Subnet collision! The network $SUBNET is already in use by the host system."
    exit 1
fi

# Check if the network is ALREADY perfectly configured on OUR interface
OWNS_NETWORK=true
if ip addr show dev "$GOPRO_IFACE" 2>/dev/null | grep -q "$HOST_IP/24"; then
    echo "ℹIP $HOST_IP/24 is already configured on $GOPRO_IFACE. Leaving it untouched on exit."
    OWNS_NETWORK=false
fi

# ---------------------------------------------------------
# 4. LIFECYCLE MANAGEMENT
# ---------------------------------------------------------
cleanup() {
    echo "Stopping container..."
    
    if [ "$OWNS_NETWORK" = true ]; then
        echo "Cleaning up network interface $GOPRO_IFACE..."
        ip addr del "${HOST_IP}/24" dev "$GOPRO_IFACE" 2>/dev/null || true
        ip link set "$GOPRO_IFACE" down 2>/dev/null || true
        echo "Host network restored cleanly."
    else
        echo "Leaving host network intact (pre-existing configuration)."
    fi
    
    if [ -n "$CHILD_PID" ]; then
        echo "Forwarding shutdown signal to Python (PID: $CHILD_PID)..."
        kill -TERM "$CHILD_PID" 2>/dev/null
        wait "$CHILD_PID" 2>/dev/null
    fi
}

# Trap Docker's SIGTERM and user SIGINT (Ctrl+C)
trap cleanup EXIT TERM INT

# ---------------------------------------------------------
# 5. APPLY NETWORKING
# ---------------------------------------------------------
if [ "$OWNS_NETWORK" = true ]; then
    echo "Linking exact host IP $HOST_IP/24 to $GOPRO_IFACE..."
    ip addr add "${HOST_IP}/24" dev "$GOPRO_IFACE" 2>/dev/null || true
    ip link set "$GOPRO_IFACE" up
fi

# ---------------------------------------------------------
# 6. LAUNCH APPLICATION
# ---------------------------------------------------------
echo "Starting main application..."

# Run gosu in the background to preserve the bash process (and its trap)
gosu "$USER_ID:$GROUP_ID" "$@" &
CHILD_PID=$!

# Wait blocks until the Python app exits or a signal is caught
wait $CHILD_PID