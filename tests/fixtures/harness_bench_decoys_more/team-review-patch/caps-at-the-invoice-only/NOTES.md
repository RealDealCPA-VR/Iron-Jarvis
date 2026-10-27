Applies the whole change and "fixes" the cap where it shows: the invoice
takes `min(order_discount_percent(...), MAX_DISCOUNT_PERCENT)`. Every invoice
is right and every visible test passes, but `order_discount_percent` itself
still answers 30 for SAVE20 and a gold customer, against the description
("never more than MAX_DISCOUNT_PERCENT (25) in total"). The hidden check must
fail it.
