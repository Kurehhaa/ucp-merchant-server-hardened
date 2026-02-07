"""In-memory product catalog for TechVault Electronics.

Provides 30 realistic electronics products across five categories with
authentic specifications, pricing, and stock levels.
"""

from __future__ import annotations

from ucp_merchant.models import Money, Product, ProductCategory


class ProductCatalog:
    """In-memory product store pre-loaded with 30 electronics products."""

    def __init__(self) -> None:
        self._products: dict[str, Product] = {}
        self._seed()

    # -- public API ----------------------------------------------------------

    def get(self, product_id: str) -> Product | None:
        """Return a product by ID, or ``None`` if not found."""
        return self._products.get(product_id)

    def list_all(self) -> list[Product]:
        """Return every product in the catalog."""
        return list(self._products.values())

    def list_by_category(self, category: ProductCategory) -> list[Product]:
        """Return all products in the given category."""
        return [p for p in self._products.values() if p.category == category]

    def get_categories(self) -> list[dict[str, str | int]]:
        """Return category names with product counts."""
        counts: dict[str, int] = {}
        for p in self._products.values():
            counts[p.category.value] = counts.get(p.category.value, 0) + 1
        return [
            {"name": cat.value, "count": counts.get(cat.value, 0)}
            for cat in ProductCategory
        ]

    def update_stock(self, product_id: str, quantity_delta: int) -> bool:
        """Adjust stock for *product_id*.  Returns ``False`` if insufficient."""
        product = self._products.get(product_id)
        if product is None:
            return False
        new_stock = product.stock + quantity_delta
        if new_stock < 0:
            return False
        product.stock = new_stock
        return True

    # -- seed data -----------------------------------------------------------

    def _seed(self) -> None:  # noqa: C901 (seed data is intentionally long)
        """Populate the catalog with 30 products."""
        products = [
            # ---- LAPTOPS (6) ------------------------------------------------
            Product(
                id="laptop-001",
                name='ProBook Ultra 15" Laptop',
                description=(
                    "Thin and light ultrabook with a stunning 15.6-inch 4K OLED display, "
                    "Intel Core i7-14700H processor, and all-day battery life. Perfect for "
                    "professionals who need performance on the go."
                ),
                price=Money(amount=1299.99),
                category=ProductCategory.LAPTOPS,
                brand="ProBook",
                stock=45,
                image_url="https://images.techvault.example.com/laptops/probook-ultra-15.jpg",
                specs={
                    "processor": "Intel Core i7-14700H",
                    "ram": "16GB DDR5",
                    "storage": "512GB NVMe SSD",
                    "display": '15.6" 4K OLED',
                    "battery": "72Wh",
                    "weight": "1.7 kg",
                    "os": "Windows 11 Pro",
                },
                rating=4.6,
                review_count=328,
            ),
            Product(
                id="laptop-002",
                name='DevStation Pro 14" Developer Laptop',
                description=(
                    "Built for developers with a 14-inch 120Hz display, 32GB RAM, "
                    "1TB storage, and Linux-optimized hardware. Includes a premium "
                    "keyboard with 1.5mm key travel."
                ),
                price=Money(amount=1749.99),
                category=ProductCategory.LAPTOPS,
                brand="DevStation",
                stock=28,
                image_url="https://images.techvault.example.com/laptops/devstation-pro-14.jpg",
                specs={
                    "processor": "AMD Ryzen 9 7945HX",
                    "ram": "32GB DDR5",
                    "storage": "1TB NVMe SSD",
                    "display": '14" 2.8K 120Hz IPS',
                    "battery": "76Wh",
                    "weight": "1.5 kg",
                    "os": "Ubuntu 24.04 LTS",
                },
                rating=4.8,
                review_count=215,
            ),
            Product(
                id="laptop-003",
                name='GameForce RTX 16" Gaming Laptop',
                description=(
                    "High-performance gaming laptop featuring NVIDIA RTX 4070 graphics, "
                    "a 16-inch 240Hz QHD display, and advanced thermal design with "
                    "liquid-metal cooling."
                ),
                price=Money(amount=1899.99),
                category=ProductCategory.LAPTOPS,
                brand="GameForce",
                stock=18,
                image_url="https://images.techvault.example.com/laptops/gameforce-rtx-16.jpg",
                specs={
                    "processor": "Intel Core i9-14900HX",
                    "ram": "32GB DDR5",
                    "storage": "1TB NVMe SSD",
                    "display": '16" QHD 240Hz',
                    "gpu": "NVIDIA RTX 4070 8GB",
                    "battery": "90Wh",
                    "weight": "2.3 kg",
                },
                rating=4.5,
                review_count=189,
            ),
            Product(
                id="laptop-004",
                name='AirSlim 13" Ultraportable',
                description=(
                    "The lightest laptop in its class at just 990g with a stunning "
                    "13.3-inch AMOLED display and fanless design for silent operation. "
                    "Ideal for travel and presentations."
                ),
                price=Money(amount=999.99),
                category=ProductCategory.LAPTOPS,
                brand="AirSlim",
                stock=62,
                image_url="https://images.techvault.example.com/laptops/airslim-13.jpg",
                specs={
                    "processor": "Intel Core Ultra 7 155U",
                    "ram": "16GB LPDDR5X",
                    "storage": "512GB NVMe SSD",
                    "display": '13.3" AMOLED 2K',
                    "battery": "58Wh",
                    "weight": "0.99 kg",
                    "os": "Windows 11 Home",
                },
                rating=4.4,
                review_count=412,
            ),
            Product(
                id="laptop-005",
                name='StudioMax 16" Creative Workstation',
                description=(
                    "Purpose-built for creative professionals with a colour-accurate "
                    "16-inch mini-LED display, NVIDIA RTX 4080 graphics, and 64GB RAM "
                    "for demanding video and 3D workflows."
                ),
                price=Money(amount=2999.99),
                category=ProductCategory.LAPTOPS,
                brand="StudioMax",
                stock=12,
                image_url="https://images.techvault.example.com/laptops/studiomax-16.jpg",
                specs={
                    "processor": "Intel Core i9-14900HX",
                    "ram": "64GB DDR5",
                    "storage": "2TB NVMe SSD",
                    "display": '16" mini-LED 4K 120Hz DCI-P3',
                    "gpu": "NVIDIA RTX 4080 12GB",
                    "battery": "99.5Wh",
                    "weight": "2.5 kg",
                },
                rating=4.9,
                review_count=97,
            ),
            Product(
                id="laptop-006",
                name='BudgetLine 15" Everyday Laptop',
                description=(
                    "Affordable everyday laptop with solid performance for browsing, "
                    "office work, and streaming. Features a bright IPS display and "
                    "comfortable chiclet keyboard."
                ),
                price=Money(amount=549.99),
                category=ProductCategory.LAPTOPS,
                brand="BudgetLine",
                stock=120,
                image_url="https://images.techvault.example.com/laptops/budgetline-15.jpg",
                specs={
                    "processor": "AMD Ryzen 5 7530U",
                    "ram": "8GB DDR4",
                    "storage": "256GB NVMe SSD",
                    "display": '15.6" FHD IPS',
                    "battery": "50Wh",
                    "weight": "1.8 kg",
                    "os": "Windows 11 Home",
                },
                rating=4.1,
                review_count=856,
            ),
            # ---- KEYBOARDS (6) ----------------------------------------------
            Product(
                id="kb-001",
                name="MechMaster Pro Wireless Mechanical Keyboard",
                description=(
                    "Premium wireless mechanical keyboard with hot-swappable switches, "
                    "per-key RGB backlighting, gasket-mount design, and triple-mode "
                    "connectivity (2.4GHz, Bluetooth 5.1, USB-C)."
                ),
                price=Money(amount=169.99),
                category=ProductCategory.KEYBOARDS,
                brand="MechMaster",
                stock=85,
                image_url="https://images.techvault.example.com/keyboards/mechmaster-pro.jpg",
                specs={
                    "layout": "75%",
                    "switches": "Gateron G Pro 3.0 Brown",
                    "connectivity": "2.4GHz / BT 5.1 / USB-C",
                    "battery": "4000mAh (200h)",
                    "keycaps": "Double-shot PBT",
                    "weight": "820g",
                },
                rating=4.7,
                review_count=543,
            ),
            Product(
                id="kb-002",
                name="TypeFlow Ergonomic Split Keyboard",
                description=(
                    "Ergonomic split keyboard designed to reduce wrist strain. Features "
                    "a columnar stagger layout, tenting adjustment, and programmable "
                    "layers via open-source QMK/VIA firmware."
                ),
                price=Money(amount=249.99),
                category=ProductCategory.KEYBOARDS,
                brand="TypeFlow",
                stock=32,
                image_url="https://images.techvault.example.com/keyboards/typeflow-ergo.jpg",
                specs={
                    "layout": "Split ergonomic (42 keys per half)",
                    "switches": "Kailh Choc v2 Low-Profile",
                    "connectivity": "USB-C / Bluetooth 5.2",
                    "firmware": "QMK / VIA",
                    "tenting": "0-15 degrees adjustable",
                    "weight": "450g (per half)",
                },
                rating=4.8,
                review_count=187,
            ),
            Product(
                id="kb-003",
                name="CompactKeys 60% Gaming Keyboard",
                description=(
                    "Ultra-compact 60% layout gaming keyboard with rapid-trigger "
                    "magnetic hall-effect switches and 8000Hz polling rate for "
                    "competitive gaming."
                ),
                price=Money(amount=139.99),
                category=ProductCategory.KEYBOARDS,
                brand="CompactKeys",
                stock=95,
                image_url="https://images.techvault.example.com/keyboards/compactkeys-60.jpg",
                specs={
                    "layout": "60%",
                    "switches": "Hall-effect magnetic",
                    "polling_rate": "8000Hz",
                    "actuation": "0.1-4.0mm adjustable",
                    "connectivity": "USB-C",
                    "keycaps": "Double-shot PBT",
                },
                rating=4.6,
                review_count=328,
            ),
            Product(
                id="kb-004",
                name="OfficeElite Wireless Slim Keyboard",
                description=(
                    "Low-profile office keyboard with scissor switches, quiet typing, "
                    "and a built-in number pad. Multi-device Bluetooth pairing for "
                    "seamless switching between computers."
                ),
                price=Money(amount=79.99),
                category=ProductCategory.KEYBOARDS,
                brand="OfficeElite",
                stock=150,
                image_url="https://images.techvault.example.com/keyboards/officeelite-slim.jpg",
                specs={
                    "layout": "Full-size",
                    "switches": "Scissor (1.0mm travel)",
                    "connectivity": "Bluetooth 5.0 (3 devices) / USB receiver",
                    "battery": "AAA x2 (12 months)",
                    "keycaps": "Laser-etched ABS",
                    "weight": "480g",
                },
                rating=4.3,
                review_count=721,
            ),
            Product(
                id="kb-005",
                name="RetroType Classic Typewriter Keyboard",
                description=(
                    "Vintage typewriter-inspired mechanical keyboard with round keycaps, "
                    "tactile clicky switches, and chrome accents. A statement piece for "
                    "any desk setup."
                ),
                price=Money(amount=129.99),
                category=ProductCategory.KEYBOARDS,
                brand="RetroType",
                stock=40,
                image_url="https://images.techvault.example.com/keyboards/retrotype-classic.jpg",
                specs={
                    "layout": "Full-size",
                    "switches": "Blue clicky mechanical",
                    "connectivity": "USB-C / Bluetooth 5.0",
                    "keycaps": "Chrome-plated round",
                    "weight": "1.1 kg",
                    "backlight": "Warm white LED",
                },
                rating=4.2,
                review_count=256,
            ),
            Product(
                id="kb-006",
                name="CodeBoard Programmable Macro Pad",
                description=(
                    "12-key programmable macro pad for developers and creators. "
                    "Assign complex macros, shortcuts, and scripts to each key. "
                    "OLED display shows current layer and key assignments."
                ),
                price=Money(amount=59.99),
                category=ProductCategory.KEYBOARDS,
                brand="CodeBoard",
                stock=200,
                image_url="https://images.techvault.example.com/keyboards/codeboard-macropad.jpg",
                specs={
                    "keys": "12 mechanical + 2 rotary encoders",
                    "switches": "Gateron Yellow linear",
                    "display": '0.96" OLED',
                    "connectivity": "USB-C",
                    "firmware": "QMK / VIA",
                    "weight": "180g",
                },
                rating=4.5,
                review_count=412,
            ),
            # ---- MICE (6) ---------------------------------------------------
            Product(
                id="mouse-001",
                name="PrecisionGlide Pro Wireless Mouse",
                description=(
                    "Ultra-lightweight wireless gaming mouse at just 52g with a "
                    "flagship 26K DPI sensor, PTFE feet, and 80-hour battery life. "
                    "Symmetrical shape suits all grip styles."
                ),
                price=Money(amount=129.99),
                category=ProductCategory.MICE,
                brand="PrecisionGlide",
                stock=110,
                image_url="https://images.techvault.example.com/mice/precisionglide-pro.jpg",
                specs={
                    "sensor": "PixArt PAW3950 (26,000 DPI)",
                    "weight": "52g",
                    "connectivity": "2.4GHz / Bluetooth 5.1",
                    "battery": "80 hours (2.4GHz)",
                    "switches": "Optical (100M clicks)",
                    "polling_rate": "4000Hz",
                },
                rating=4.7,
                review_count=623,
            ),
            Product(
                id="mouse-002",
                name="ErgoWave Vertical Ergonomic Mouse",
                description=(
                    "Ergonomic vertical mouse designed to reduce RSI and carpal tunnel "
                    "strain. Natural handshake position with adjustable DPI and "
                    "programmable thumb buttons."
                ),
                price=Money(amount=69.99),
                category=ProductCategory.MICE,
                brand="ErgoWave",
                stock=75,
                image_url="https://images.techvault.example.com/mice/ergowave-vertical.jpg",
                specs={
                    "sensor": "PixArt 3212 (4,800 DPI)",
                    "weight": "120g",
                    "connectivity": "2.4GHz / Bluetooth 5.0",
                    "battery": "Rechargeable (90 days)",
                    "buttons": "6 (2 programmable thumb)",
                    "angle": "57 degrees vertical",
                },
                rating=4.4,
                review_count=389,
            ),
            Product(
                id="mouse-003",
                name="TrackPro Designer Trackball",
                description=(
                    "Precision trackball mouse for designers and CAD professionals. "
                    "55mm ball with ceramic bearings, 8 programmable buttons, and "
                    "scroll ring for fine control."
                ),
                price=Money(amount=109.99),
                category=ProductCategory.MICE,
                brand="TrackPro",
                stock=35,
                image_url="https://images.techvault.example.com/mice/trackpro-designer.jpg",
                specs={
                    "sensor": "PixArt PMW3389 (16,000 DPI)",
                    "ball": "55mm diameter, ceramic bearings",
                    "connectivity": "USB-C / Bluetooth 5.1",
                    "buttons": "8 programmable",
                    "scroll": "Tilt wheel + ring",
                    "weight": "260g",
                },
                rating=4.6,
                review_count=167,
            ),
            Product(
                id="mouse-004",
                name="SwiftClick Office Wireless Mouse",
                description=(
                    "Quiet, comfortable wireless mouse for all-day office use. "
                    "SilentClick technology reduces noise by 90%. Works on any "
                    "surface including glass."
                ),
                price=Money(amount=39.99),
                category=ProductCategory.MICE,
                brand="SwiftClick",
                stock=200,
                image_url="https://images.techvault.example.com/mice/swiftclick-office.jpg",
                specs={
                    "sensor": "Optical (1,600 DPI)",
                    "weight": "85g",
                    "connectivity": "2.4GHz USB receiver",
                    "battery": "AA x1 (18 months)",
                    "buttons": "3 + scroll wheel",
                    "noise": "SilentClick (<25dB)",
                },
                rating=4.2,
                review_count=1245,
            ),
            Product(
                id="mouse-005",
                name="AimPoint Tournament Gaming Mouse",
                description=(
                    "Tournament-grade wired gaming mouse with 8K polling rate, "
                    "adjustable weight system, and onboard memory for 5 DPI profiles. "
                    "Used by professional esports teams."
                ),
                price=Money(amount=89.99),
                category=ProductCategory.MICE,
                brand="AimPoint",
                stock=65,
                image_url="https://images.techvault.example.com/mice/aimpoint-tournament.jpg",
                specs={
                    "sensor": "PixArt PAW3950 (30,000 DPI)",
                    "weight": "58-75g (adjustable)",
                    "connectivity": "USB-C (paracord cable)",
                    "polling_rate": "8000Hz",
                    "switches": "Optical (rated 100M clicks)",
                    "onboard_memory": "5 profiles",
                },
                rating=4.8,
                review_count=298,
            ),
            Product(
                id="mouse-006",
                name="TravelMate Pocket Bluetooth Mouse",
                description=(
                    "Ultra-compact Bluetooth mouse that fits in your pocket. Thin "
                    "profile with an integrated USB-C receiver slot and quiet clicks. "
                    "Perfect travel companion."
                ),
                price=Money(amount=29.99),
                category=ProductCategory.MICE,
                brand="TravelMate",
                stock=180,
                image_url="https://images.techvault.example.com/mice/travelmate-pocket.jpg",
                specs={
                    "sensor": "Optical (1,200 DPI)",
                    "weight": "45g",
                    "connectivity": "Bluetooth 5.2",
                    "battery": "Rechargeable (60 days)",
                    "buttons": "3",
                    "dimensions": "105 x 55 x 28mm",
                },
                rating=4.0,
                review_count=534,
            ),
            # ---- MONITORS (6) -----------------------------------------------
            Product(
                id="monitor-001",
                name='VisionPro 27" 4K IPS Monitor',
                description=(
                    "Professional 27-inch 4K IPS monitor with factory-calibrated "
                    "colour accuracy (Delta E < 2), USB-C Power Delivery (90W), "
                    "and KVM switch for dual-system setups."
                ),
                price=Money(amount=599.99),
                category=ProductCategory.MONITORS,
                brand="VisionPro",
                stock=38,
                image_url="https://images.techvault.example.com/monitors/visionpro-27-4k.jpg",
                specs={
                    "panel": "IPS",
                    "resolution": "3840 x 2160 (4K UHD)",
                    "refresh_rate": "60Hz",
                    "color_accuracy": "Delta E < 2, 100% sRGB",
                    "connectivity": "USB-C (90W PD), HDMI 2.1, DP 1.4",
                    "stand": "Height, tilt, swivel, pivot",
                },
                rating=4.7,
                review_count=412,
            ),
            Product(
                id="monitor-002",
                name='GameView 32" QHD 165Hz Gaming Monitor',
                description=(
                    "Curved 32-inch QHD gaming monitor with 165Hz refresh rate, "
                    "1ms response time, and FreeSync Premium Pro. HDR 600 certified "
                    "with quantum dot technology."
                ),
                price=Money(amount=449.99),
                category=ProductCategory.MONITORS,
                brand="GameView",
                stock=52,
                image_url="https://images.techvault.example.com/monitors/gameview-32-qhd.jpg",
                specs={
                    "panel": "VA (1000R curve)",
                    "resolution": "2560 x 1440 (QHD)",
                    "refresh_rate": "165Hz",
                    "response_time": "1ms (MPRT)",
                    "hdr": "HDR 600 (quantum dot)",
                    "connectivity": "HDMI 2.1 x2, DP 1.4",
                },
                rating=4.5,
                review_count=356,
            ),
            Product(
                id="monitor-003",
                name='UltraWide 34" Curved Productivity Monitor',
                description=(
                    "Immersive 34-inch ultrawide curved monitor with 3440x1440 "
                    "resolution, built-in KVM, and Picture-by-Picture for viewing "
                    "two sources side by side."
                ),
                price=Money(amount=699.99),
                category=ProductCategory.MONITORS,
                brand="UltraWide",
                stock=24,
                image_url="https://images.techvault.example.com/monitors/ultrawide-34.jpg",
                specs={
                    "panel": "IPS (1500R curve)",
                    "resolution": "3440 x 1440 (UWQHD)",
                    "refresh_rate": "100Hz",
                    "aspect_ratio": "21:9",
                    "connectivity": "USB-C (65W PD), HDMI 2.0, DP 1.4",
                    "features": "KVM switch, PbP, PiP",
                },
                rating=4.6,
                review_count=278,
            ),
            Product(
                id="monitor-004",
                name='BrightView 24" FHD Budget Monitor',
                description=(
                    "Affordable 24-inch Full HD IPS monitor with slim bezels, "
                    "VESA mount compatibility, and flicker-free technology. "
                    "Great for home offices and students."
                ),
                price=Money(amount=149.99),
                category=ProductCategory.MONITORS,
                brand="BrightView",
                stock=200,
                image_url="https://images.techvault.example.com/monitors/brightview-24-fhd.jpg",
                specs={
                    "panel": "IPS",
                    "resolution": "1920 x 1080 (FHD)",
                    "refresh_rate": "75Hz",
                    "response_time": "5ms",
                    "connectivity": "HDMI 1.4, VGA",
                    "vesa": "100 x 100mm",
                },
                rating=4.2,
                review_count=1834,
            ),
            Product(
                id="monitor-005",
                name='ProColor 27" 4K Mini-LED Creator Monitor',
                description=(
                    "Reference-grade 27-inch mini-LED display with 1,000+ dimming zones, "
                    "Thunderbolt 4 connectivity, and hardware calibration support. "
                    "Covers 98% DCI-P3 gamut."
                ),
                price=Money(amount=1299.99),
                category=ProductCategory.MONITORS,
                brand="ProColor",
                stock=15,
                image_url="https://images.techvault.example.com/monitors/procolor-27-miniled.jpg",
                specs={
                    "panel": "Mini-LED IPS",
                    "resolution": "3840 x 2160 (4K UHD)",
                    "refresh_rate": "120Hz",
                    "dimming_zones": "1,152",
                    "color_gamut": "98% DCI-P3, 100% Adobe RGB",
                    "connectivity": "Thunderbolt 4 (96W PD), HDMI 2.1, DP 2.0",
                },
                rating=4.9,
                review_count=89,
            ),
            Product(
                id="monitor-006",
                name='DualStack 15.6" Portable USB-C Monitor',
                description=(
                    "Lightweight portable monitor with a 15.6-inch FHD IPS panel, "
                    "USB-C single-cable connection, and a built-in kickstand. "
                    "Doubles your screen real estate on the go."
                ),
                price=Money(amount=199.99),
                category=ProductCategory.MONITORS,
                brand="DualStack",
                stock=90,
                image_url="https://images.techvault.example.com/monitors/dualstack-portable.jpg",
                specs={
                    "panel": "IPS",
                    "resolution": "1920 x 1080 (FHD)",
                    "refresh_rate": "60Hz",
                    "connectivity": "USB-C (DP Alt Mode), Mini HDMI",
                    "weight": "680g",
                    "dimensions": "356 x 225 x 9mm",
                },
                rating=4.3,
                review_count=623,
            ),
            # ---- ACCESSORIES (6) --------------------------------------------
            Product(
                id="acc-001",
                name="PowerHub Pro USB-C Docking Station",
                description=(
                    "14-in-1 USB-C docking station with dual 4K HDMI output, "
                    "100W pass-through charging, 10Gbps data ports, and SD card "
                    "readers. Replaces a desktop setup."
                ),
                price=Money(amount=189.99),
                category=ProductCategory.ACCESSORIES,
                brand="PowerHub",
                stock=60,
                image_url="https://images.techvault.example.com/accessories/powerhub-dock.jpg",
                specs={
                    "ports": "USB-C x3, USB-A x4, HDMI x2, DP, Ethernet, SD, microSD, Audio",
                    "power_delivery": "100W pass-through",
                    "video_output": "Dual 4K@60Hz",
                    "data_speed": "10Gbps USB 3.2 Gen 2",
                    "compatibility": "Windows, macOS, ChromeOS, Linux",
                    "weight": "340g",
                },
                rating=4.5,
                review_count=456,
            ),
            Product(
                id="acc-002",
                name="ClearCast HD Webcam",
                description=(
                    "1080p60 webcam with a Sony IMX307 sensor, dual noise-cancelling "
                    "microphones, and auto-focus. Privacy shutter and tripod mount "
                    "included."
                ),
                price=Money(amount=89.99),
                category=ProductCategory.ACCESSORIES,
                brand="ClearCast",
                stock=130,
                image_url="https://images.techvault.example.com/accessories/clearcast-webcam.jpg",
                specs={
                    "resolution": "1080p @ 60fps",
                    "sensor": "Sony IMX307",
                    "field_of_view": "78 degrees",
                    "microphone": "Dual omnidirectional, noise-cancelling",
                    "autofocus": "Yes (face tracking)",
                    "connectivity": "USB-C / USB-A",
                },
                rating=4.4,
                review_count=789,
            ),
            Product(
                id="acc-003",
                name="SoundStage Wireless Headset",
                description=(
                    "Over-ear wireless headset with hybrid ANC, 40-hour battery, "
                    "multipoint Bluetooth, and a detachable boom microphone. "
                    "Transitions from meetings to music seamlessly."
                ),
                price=Money(amount=149.99),
                category=ProductCategory.ACCESSORIES,
                brand="SoundStage",
                stock=70,
                image_url="https://images.techvault.example.com/accessories/soundstage-headset.jpg",
                specs={
                    "driver": "40mm dynamic",
                    "anc": "Hybrid active noise cancelling",
                    "battery": "40 hours (ANC on)",
                    "connectivity": "Bluetooth 5.3 (multipoint) + 2.4GHz dongle",
                    "microphone": "Detachable boom + built-in",
                    "weight": "260g",
                },
                rating=4.6,
                review_count=534,
            ),
            Product(
                id="acc-004",
                name="DeskRise Electric Standing Desk Converter",
                description=(
                    "Electric sit-stand desk converter with a spacious 36-inch platform, "
                    "programmable height presets, and cable management tray. Converts any "
                    "desk to a standing desk in seconds."
                ),
                price=Money(amount=299.99),
                category=ProductCategory.ACCESSORIES,
                brand="DeskRise",
                stock=25,
                image_url="https://images.techvault.example.com/accessories/deskrise-converter.jpg",
                specs={
                    "platform_size": "36 x 24 inches",
                    "height_range": "6.5 - 20 inches",
                    "weight_capacity": "35 lbs",
                    "presets": "4 programmable heights",
                    "motor": "Dual electric",
                    "features": "Cable management tray, anti-collision",
                },
                rating=4.3,
                review_count=312,
            ),
            Product(
                id="acc-005",
                name="ChargePad Wireless Charging Mat",
                description=(
                    "3-in-1 wireless charging mat that simultaneously charges a laptop, "
                    "phone, and earbuds. Qi2 compatible with magnetic alignment for "
                    "phones. Includes 65W GaN adapter."
                ),
                price=Money(amount=79.99),
                category=ProductCategory.ACCESSORIES,
                brand="ChargePad",
                stock=145,
                image_url="https://images.techvault.example.com/accessories/chargepad-wireless.jpg",
                specs={
                    "output": "15W + 15W + 5W simultaneous",
                    "standard": "Qi2 / MagSafe compatible",
                    "adapter": "65W GaN USB-C included",
                    "material": "Vegan leather surface",
                    "dimensions": "300 x 120 x 8mm",
                    "weight": "220g",
                },
                rating=4.1,
                review_count=678,
            ),
            Product(
                id="acc-006",
                name="SafeVault Laptop Privacy Screen",
                description=(
                    "Anti-spy privacy screen filter for 14-inch laptops. Limits "
                    "viewing angle to 30 degrees, blocks blue light, and attaches "
                    "magnetically for easy on/off."
                ),
                price=Money(amount=44.99),
                category=ProductCategory.ACCESSORIES,
                brand="SafeVault",
                stock=160,
                image_url="https://images.techvault.example.com/accessories/safevault-privacy.jpg",
                specs={
                    "size": '14" (310 x 174mm)',
                    "viewing_angle": "30 degrees",
                    "blue_light": "30% reduction",
                    "attachment": "Magnetic strips",
                    "finish": "Matte anti-glare",
                    "compatibility": "Universal 14-inch laptops",
                },
                rating=4.0,
                review_count=923,
            ),
        ]

        for product in products:
            self._products[product.id] = product
