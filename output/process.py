import json
import sys


def build_bit_to_name_map(modules):
    bit_to_name = {}
    for module in modules.values():
        for name, net in module.get("netnames", {}).items():
            for b in net.get("bits", []):
                if b not in bit_to_name:
                    bit_to_name[b] = name
    return bit_to_name


def bits_to_signal(bits, bit_map):
    if not bits:
        return "None"

    names = []
    for b in bits:
        if b in bit_map:
            names.append(bit_map[b])
        else:
            names.append(str(b))

    return "_".join(sorted(set(names)))


def analyze_per_module(json_file):
    with open(json_file) as f:
        data = json.load(f)

    modules = data.get("modules", {})
    bit_map = build_bit_to_name_map(modules)

    total_clock_usage = 0
    total_async_reset_usage = 0
    total_sync_reset_usage = 0

    module_summary = {}

    for module_name, module in modules.items():
        # ===== 按“名字”去重（module级）=====
        clk_names = set()
        async_reset_names = set()
        sync_reset_names = set()

        for cell in module.get("cells", {}).values():
            cell_type = cell["type"]
            conns = cell.get("connections", {})

            if any(x in cell_type for x in ["$dff", "$adff", "$sdff"]):

                # ===== clock =====
                clk_name = bits_to_signal(conns.get("CLK", []), bit_map)
                if clk_name not in ["None", "x"]:
                    clk_names.add(clk_name)

                # ===== async reset =====
                arst_name = bits_to_signal(conns.get("ARST", []), bit_map)
                if arst_name not in ["None", "x"]:
                    async_reset_names.add(arst_name)

                # ===== sync reset =====
                srst_name = bits_to_signal(conns.get("SRST", []), bit_map)
                if srst_name not in ["None", "x"]:
                    sync_reset_names.add(srst_name)

        module_summary[module_name] = {
            "clocks": clk_names,
            "async_resets": async_reset_names,
            "sync_resets": sync_reset_names
        }

        total_clock_usage += len(clk_names)
        total_async_reset_usage += len(async_reset_names)
        total_sync_reset_usage += len(sync_reset_names)

    # ===== 输出 =====
    print("\n====== PER MODULE STATS (DEDUP BY NAME) ======\n")

    for module_name, info in module_summary.items():
        print(f"[{module_name}]")

        print(f"  Clock count        : {len(info['clocks'])}")
        for c in sorted(info["clocks"]):
            print(f"    CLK  : {c}")

        print(f"  Async reset count  : {len(info['async_resets'])}")
        for r in sorted(info["async_resets"]):
            print(f"    ARST : {r}")

        print(f"  Sync reset count   : {len(info['sync_resets'])}")
        for r in sorted(info["sync_resets"]):
            print(f"    SRST : {r}")

        print()

    print("====== TOTAL (SUM OVER MODULES) ======")
    print(f"Clock usage total        : {total_clock_usage}")
    print(f"Async reset usage total  : {total_async_reset_usage}")
    print(f"Sync reset usage total   : {total_sync_reset_usage}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python analyze_module_clock.py design.json")
        sys.exit(1)

    analyze_per_module(sys.argv[1])