"""One checkout, start to finish."""

from models import Cart, Customer, Line
from shop import checkout

cart = Cart(lines=[Line("pen", 12, 2.00), Line("pad", 4, 4.00)], coupon="SAVE5")
customer = Customer(member=True, tax_rate=0.08)

if __name__ == "__main__":
    print(checkout(cart, customer))
