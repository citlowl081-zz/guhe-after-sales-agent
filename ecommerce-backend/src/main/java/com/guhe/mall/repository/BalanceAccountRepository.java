package com.guhe.mall.repository;

import com.guhe.mall.entity.BalanceAccount;
import java.util.Optional;
import org.springframework.data.jpa.repository.JpaRepository;

public interface BalanceAccountRepository extends JpaRepository<BalanceAccount, Long> {

    Optional<BalanceAccount> findByUserId(String userId);
}
