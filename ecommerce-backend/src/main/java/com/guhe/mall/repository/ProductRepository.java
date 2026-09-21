package com.guhe.mall.repository;

import com.guhe.mall.entity.Product;
import java.util.List;
import java.util.Optional;
import org.springframework.data.jpa.repository.JpaRepository;

public interface ProductRepository extends JpaRepository<Product, Long> {

    List<Product> findByNameContainingIgnoreCaseOrDescriptionContainingIgnoreCase(String nameKeyword, String descriptionKeyword);

    Optional<Product> findByCode(String code);

    List<Product> findByActiveTrue();

    List<Product> findByActive(Boolean active);
}
