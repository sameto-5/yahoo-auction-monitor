from dataclasses import dataclass, field


@dataclass
class AuctionItem:
    item_id: str
    title: str
    price: int | None
    url: str
    seller: str = ""
    store_condition: str = ""
    description: str = ""
    status: str = "active"
    status_class: str = "unknown"
    status_reason: str = "状態を判断できる表現なし"
    matched_rule_ids: list[str] = field(default_factory=list)
    listing_type: str = "unknown"
    buy_now_price: int | None = None
    shipping_fee: int | None = None
    end_at: str = ""

    def as_dict(self):
        return dict(self.__dict__)
