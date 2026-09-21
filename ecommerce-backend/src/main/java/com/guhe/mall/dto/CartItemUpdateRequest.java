package com.guhe.mall.dto;

import jakarta.validation.constraints.Min;

public record CartItemUpdateRequest(
    @Min(1) Integer quantity,
    Boolean selected
) {
}
